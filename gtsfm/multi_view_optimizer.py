"""Optimizer which performs averaging and bundle adjustment on all images in the scene.

Authors: Ayush Baid, John Lambert
"""
from typing import Dict, List, Optional, Tuple, Mapping, Union

import dask
import numpy as np
from dask.delayed import Delayed
from gtsam import Pose3, Rot3, Unit3

import gtsfm.common.types as gtsfm_types
import gtsfm.utils.graph as graph_utils
from gtsfm.averaging.rotation.rotation_averaging_base import RotationAveragingBase
from gtsfm.averaging.translation.translation_averaging_base import TranslationAveragingBase
from gtsfm.bundle.bundle_adjustment import BundleAdjustmentOptimizer
from gtsfm.common.pose_prior import PosePrior
from gtsfm.data_association.data_assoc import DataAssociation
from gtsfm.pose_slam.pose_slam import PoseSlam


class MultiViewOptimizer:
    def __init__(
        self,
        rot_avg_module: RotationAveragingBase,
        trans_avg_module: TranslationAveragingBase,
        data_association_module: DataAssociation,
        bundle_adjustment_module: BundleAdjustmentOptimizer,
        use_pose_slam_intitialization: bool = False,
    ) -> None:
        self.pose_slam_module = PoseSlam()
        self.rot_avg_module = rot_avg_module
        self.trans_avg_module = trans_avg_module
        self.data_association_module = data_association_module
        self.ba_optimizer = bundle_adjustment_module
        self._use_pose_slam_intitialization = use_pose_slam_intitialization

    def create_computation_graph(
        self,
        num_images: int,
        keypoints_graph: List[Delayed],
        i2Ri1_graph: Mapping[Tuple[int, int], Union[Delayed, Optional[Rot3]]],
        i2Ui1_graph: Mapping[Tuple[int, int], Union[Delayed, Optional[Unit3]]],
        v_corr_idxs_graph: Mapping[Tuple[int, int], Union[Delayed, Optional[np.ndarray]]],
        all_intrinsics: List[Optional[gtsfm_types.CALIBRATION_TYPE]],
        absolute_pose_priors: List[Optional[PosePrior]],
        relative_pose_priors: Dict[Tuple[int, int], PosePrior],
        cameras_gt: List[Optional[gtsfm_types.CAMERA_TYPE]],
        gt_wTi_list: List[Optional[Pose3]],
        images_graph: Optional[List[Delayed]] = None,
    ) -> Tuple[Delayed, Delayed, list]:
        """Creates a computation graph for multi-view optimization.

        Args:
            num_images: number of images in the scene.
            keypoints_graph: keypoints for images, each wrapped up as Delayed.
            i2Ri1_graph: relative rotations for image pairs, each value wrapped up as Delayed.
            i2Ui1_graph: relative unit-translations for image pairs, each value wrapped up as Delayed.
            v_corr_idxs_graph: indices of verified correspondences for image pairs, wrapped up as Delayed.
            all_intrinsics: intrinsics for images.
            absolute_pose_priors: priors on the camera poses (not delayed).
            relative_pose_priors: priors on the pose between camera pairs (not delayed)
            cameras_gt: list of GT cameras (if they exist), ordered by camera index.
            gt_wTi_list: list of GT poses of the camera.
            images_graph (optional): list of images. Defaults to None.

        Returns:
            The GtsfmData input to bundle adjustment, aligned to GT (if provided), wrapped up as Delayed.
            The final output GtsfmData, wrapped up as Delayed.
            List of GtsfmMetricGroups from different modules, wrapped up as Delayed.
        """
        # prune the graph to a single connected component.
        pruned_i2Ri1_graph, pruned_i2Ui1_graph = dask.delayed(graph_utils.prune_to_largest_connected_component, nout=2)(
            dask.delayed(i2Ri1_graph), dask.delayed(i2Ui1_graph), relative_pose_priors
        )

        if self._use_pose_slam_intitialization:
            delayed_poses, pose_slam_metrics = self.pose_slam_module.create_computation_graph(
                num_images, relative_pose_priors=relative_pose_priors, gt_wTi_list=gt_wTi_list
            )
            multiview_optimizer_metrics_graph = [
                pose_slam_metrics
            ]
        else:
            delayed_wRi, rot_avg_metrics = self.rot_avg_module.create_computation_graph(
                num_images, pruned_i2Ri1_graph, relative_pose_priors=relative_pose_priors, gt_wTi_list=gt_wTi_list
            )

            delayed_poses, ta_metrics = self.trans_avg_module.create_computation_graph(
                num_images,
                pruned_i2Ui1_graph,
                delayed_wRi,
                absolute_pose_priors,
                relative_pose_priors,
                gt_wTi_list=gt_wTi_list,
            )
            multiview_optimizer_metrics_graph = [
                rot_avg_metrics,
                ta_metrics,
            ]
        init_cameras_graph = dask.delayed(init_cameras)(delayed_poses, all_intrinsics)

        ba_input_graph, data_assoc_metrics_graph = self.data_association_module.create_computation_graph(
            num_images,
            init_cameras_graph,
            v_corr_idxs_graph,
            keypoints_graph,
            cameras_gt,
            relative_pose_priors,
            images_graph,
        )

        ba_result_graph, ba_metrics_graph = self.ba_optimizer.create_computation_graph(
            ba_input_graph, absolute_pose_priors, relative_pose_priors, cameras_gt
        )

        multiview_optimizer_metrics_graph += [
            data_assoc_metrics_graph,
            ba_metrics_graph,
        ]

        # align the sparse multi-view estimate before BA to the ground truth pose graph.
        ba_input_graph = dask.delayed(ba_input_graph.align_via_Sim3_to_poses)(gt_wTi_list)

        return ba_input_graph, ba_result_graph, multiview_optimizer_metrics_graph


def init_cameras(
    wTi_list: List[Optional[Pose3]],
    intrinsics_list: List[gtsfm_types.CALIBRATION_TYPE],
) -> Dict[int, gtsfm_types.CAMERA_TYPE]:
    """Generate camera from valid rotations and unit-translations.

    Args:
        wRi_list: rotations for cameras.
        wti_list: translations for cameras.
        intrinsics_list: intrinsics for cameras.

    Returns:
        Valid cameras.
    """
    camera_class = gtsfm_types.get_camera_class_for_calibration(intrinsics_list[0])
    return {idx: camera_class(wTi, intrinsics_list[idx]) for idx, wTi in enumerate(wTi_list) if wTi is not None}
