"""Base class for the V (verification) stage of the frontend.

Authors: Ayush Baid
"""
import abc
from typing import Dict, List, Optional, Tuple

import dask
import numpy as np
from dask.delayed import Delayed
from gtsam import Cal3Bundler, EssentialMatrix


class VerifierBase(metaclass=abc.ABCMeta):
    """Base class for all verifiers.

    Verifiers take the coordinates of the matches as inputs and returns the
    estimated essential matrix as well as geometrically verified points.
    """

    def __init__(self, min_pts):
        self.min_pts = min_pts

    @abc.abstractmethod
    def verify_with_exact_intrinsics(
        self,
        keypoints_i1: np.ndarray,
        keypoints_i2: np.ndarray,
        match_indices: np.ndarray,
        camera_intrinsics_i1: Cal3Bundler,
        camera_intrinsics_i2: Cal3Bundler,
    ) -> Tuple[Optional[EssentialMatrix], np.ndarray]:
        """Estimates the essential matrix and verifies the feature matches.

        Note: this function is prefered when camera intrinsics are known. The
        feature coordinates are normalized and the essential matrix is directly
        estimated.

        Args:
            keypoints_i1: detected features in image #i1, of shape (N1, 2+).
            keypoints_i2: detected features in image #i2, of shape (N2, 2+).
            match_indices: matches as indices of features from both images, of
                           shape (N3, 2), where N3 <= min(N1, N2).
            camera_intrinsics_i1: intrinsics for image #i1.
            camera_intrinsics_i2: intrinsics for image #i2.

        Returns:
            Estimated essential matrix i2Ei1, or None if it cannot be estimated.
            Indices of verified correspondences, of shape (N, 2) with N <= N3.
                These indices are subset of match_indices.
        """

    @abc.abstractmethod
    def verify_with_approximate_intrinsics(
        self,
        keypoints_i1: np.ndarray,
        keypoints_i2: np.ndarray,
        match_indices: np.ndarray,
        camera_intrinsics_i1: Cal3Bundler,
        camera_intrinsics_i2: Cal3Bundler,
    ) -> Tuple[Optional[EssentialMatrix], np.ndarray]:
        """Estimates the essential matrix and verifies the feature matches.

        Note: this function is preferred when camera intrinsics are approximate
        (i.e from image size/exif). The feature coordinates are used to compute
        the fundamental matrix, which is then converted to the essential matrix.

        Args:
            keypoints_i1: detected features in image #i1, of shape (N1, 2+).
            keypoints_i2: detected features in image #i2, of shape (N2, 2+).
            match_indices: matches as indices of features from both images, of
                           shape (N3, 2), where N3 <= min(N1, N2).
            camera_intrinsics_i1: intrinsics for image #i1.
            camera_intrinsics_i2: intrinsics for image #i2.

        Returns:
            Estimated essential matrix i2Ei1, or None if it cannot be estimated.
            Indices of verified correspondences, of shape (N, 2) with N <= N3.
                These indices are subset of match_indices.
        """

    def create_computation_graph(self,
                                 detection_graph: List[Delayed],
                                 matcher_graph: Dict[Tuple[int, int], Delayed],
                                 camera_intrinsics_graph: List[Delayed],
                                 exact_intrinsics_flag: bool = True
                                 ) -> Dict[Tuple[int, int], Delayed]:
        """Generates the computation graph to perform verification of putative
        correspondences.

        Args:
            detection_graph: nodes with features for each image.
            matcher_graph: nodes with matching results for pairs of images.
            camera_intrinsics_graph: nodes with intrinsics for each image.
            exact_intrinsics_flag (optional): flag denoting if intrinsics are
                                              exact, and an essential matrix
                                              can be directly computed.
                                              Defaults to True.

        Returns:
            delayed dask elements for verification.
        """

        result = dict()

        fn_to_use = self.verify_with_exact_intrinsics if exact_intrinsics_flag \
            else self.verify_with_approximate_intrinsics

        for image_idxes, delayed_matcher in matcher_graph.items():
            result[image_idxes] = dask.delayed(fn_to_use)(
                detection_graph[image_idxes[0]],
                detection_graph[image_idxes[1]],
                delayed_matcher,
                camera_intrinsics_graph[image_idxes[0]],
                camera_intrinsics_graph[image_idxes[1]],
            )

        return result
