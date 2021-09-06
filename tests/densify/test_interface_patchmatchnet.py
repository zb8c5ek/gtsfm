"""Unit tests for the interface for PatchmatchNet

In this unit test, we make a simple scenario that eight cameras are around a circle (radius = 40.0).
Every camera's pose is towards the center of the circle (0, 0, 0). 100 track points are set on the camera plane or 
upper the camera plane.

Authors: Ren Liu
"""
import unittest

import numpy as np
from gtsam import PinholeCameraCal3_S2, Cal3_S2, Point3
from gtsam.examples import SFMdata

from gtsfm.common.image import Image
from gtsfm.common.gtsfm_data import GtsfmData, SfmTrack
from gtsfm.densify.patchmatchnet_data import PatchmatchNetData, MIN_DEPTH_PERCENTILE, MAX_DEPTH_PERCENTILE

# set the default image size as 800x600, with 3 channels
DEFAULT_IMAGE_W = 800
DEFAULT_IMAGE_H = 600
DEFAULT_IMAGE_C = 3

# set default track points, the coordinates are in the world frame
DEFAULT_NUM_TRACKS = 100
DEFAULT_TRACK_POINTS = [Point3(5, 5, float(i)) for i in range(DEFAULT_NUM_TRACKS)]

# set default camera intrinsics
DEFAULT_CAMERA_INTRINSICS = Cal3_S2(
    fx=100.0,
    fy=100.0,
    s=1.0,
    u0=DEFAULT_IMAGE_W // 2,
    v0=DEFAULT_IMAGE_H // 2,
)
# set default camera poses as described in GTSAM example
DEFAULT_CAMERA_POSES = SFMdata.createPoses(DEFAULT_CAMERA_INTRINSICS)
# set default camera instances
DEFAULT_CAMERAS = [
    PinholeCameraCal3_S2(DEFAULT_CAMERA_POSES[i], DEFAULT_CAMERA_INTRINSICS) for i in range(len(DEFAULT_CAMERA_POSES))
]
DEFAULT_NUM_CAMERAS = len(DEFAULT_CAMERAS)
# the number of valid images should be equal to the number of cameras (with estimated pose)
DEFAULT_NUM_IMAGES = DEFAULT_NUM_CAMERAS

# build dummy image dictionary with default image shape
DEFAULT_DUMMY_IMAGE_DICT = {
    i: Image(value_array=np.zeros([DEFAULT_IMAGE_H, DEFAULT_IMAGE_W, DEFAULT_IMAGE_C], dtype=int))
    for i in range(DEFAULT_NUM_IMAGES)
}

# set camera[1] to be selected in test_get_item
EXAMPLE_CAMERA_ID = 1


class TestPatchmatchNetData(unittest.TestCase):
    """Unit tests for the interface for PatchmatchNet."""

    def setUp(self) -> None:
        """Set up the image dictionary and gtsfm result for the test."""
        super().setUp()

        # set the number of images as the default number
        self._num_images = DEFAULT_NUM_IMAGES

        # set up the default image dictionary
        self._img_dict = DEFAULT_DUMMY_IMAGE_DICT

        # initialize an empty sfm result in GtsfmData class
        self._sfm_result = GtsfmData(self._num_images)
        # add default cameras to the sfm result
        self.add_default_cameras()
        # add default tracks to the sfm result
        self.add_default_tracks()
        # build PatchmatchNet dataset from input images and the sfm result
        self._dataset_patchmatchnet = PatchmatchNetData(self._img_dict, self._sfm_result)

    def add_default_cameras(self) -> None:
        """Assume all default cameras are valid cameras, add them into sfm_result"""
        self._num_valid_cameras = DEFAULT_NUM_CAMERAS
        for i in range(DEFAULT_NUM_CAMERAS):
            self._sfm_result.add_camera(i, DEFAULT_CAMERAS[i])

    def add_default_tracks(self) -> None:
        """Calculate the measurements under each camera for all track points, then insert the tracks into sfm result"""

        self._num_tracks = DEFAULT_NUM_TRACKS
        for j in range(self._num_tracks):
            world_x = DEFAULT_TRACK_POINTS[j]
            track_to_add = SfmTrack(world_x)

            for i in range(self._num_valid_cameras):
                measurement = self._sfm_result.get_camera(i).project(world_x)
                track_to_add.add_measurement(idx=i, m=measurement)
            self._sfm_result.add_track(track_to_add)

    def test_dataset_length(self) -> None:
        """Test whether the dataset length is equal to the number of valid cameras, because every valid camera will be
        regarded as reference view once."""
        self.assertEqual(len(self._dataset_patchmatchnet), self._num_valid_cameras)

    def test_select_src_views(self) -> None:
        """Test whether the (ref_view, src_view) pairs are selected correctly."""
        self.assertTrue(
            self._dataset_patchmatchnet.get_packed_pairs()
            == [
                {"ref_id": 0, "src_ids": [7, 1, 6, 2]},
                {"ref_id": 1, "src_ids": [0, 2, 3, 7]},
                {"ref_id": 2, "src_ids": [3, 1, 4, 0]},
                {"ref_id": 3, "src_ids": [4, 2, 5, 1]},
                {"ref_id": 4, "src_ids": [5, 3, 6, 2]},
                {"ref_id": 5, "src_ids": [6, 4, 3, 7]},
                {"ref_id": 6, "src_ids": [5, 7, 4, 0]},
                {"ref_id": 7, "src_ids": [6, 0, 5, 1]},
            ]
        )

    def test_depth_ranges(self) -> None:
        """Test whether the depth ranges for each camera are calculated correctly and whether the depth outliers
        (one too close and one too far) are filtered out in the depth range"""

        # test the lower bound of the depth range
        self.assertAlmostEqual(self._dataset_patchmatchnet._depth_ranges[EXAMPLE_CAMERA_ID][0], 10.60262, 2)
        # test the upper bound of the depth range
        self.assertAlmostEqual(self._dataset_patchmatchnet._depth_ranges[EXAMPLE_CAMERA_ID][1], 34.12857, 2)

    def test_get_item(self) -> None:
        """Test get item method when yielding test data from dataset."""
        example = self._dataset_patchmatchnet[EXAMPLE_CAMERA_ID]

        B, C, H, W = example["imgs"]["stage_0"].shape

        # the batch size here means the number of views (1 ref view and (B-1) src views) used in inference.
        #   the number of views must not be larger than the number of valid cameras, or the number of images
        self.assertLessEqual(B, DEFAULT_NUM_IMAGES)

        # test images size and channel number are correct
        h, w, c = self._img_dict[0].value_array.shape
        self.assertEqual((H, W, C), (h, w, c))

        # test 3x4 projection matrices are correct
        sample_proj_mat = example["proj_matrices"]["stage_0"][0][:3, :4]
        sample_camera = self._sfm_result.get_camera(EXAMPLE_CAMERA_ID)
        actual_proj_mat = sample_camera.calibration().K() @ sample_camera.pose().inverse().matrix()[:3, :4]
        self.assertTrue(np.array_equal(sample_proj_mat, actual_proj_mat))


if __name__ == "__main__":
    unittest.main()
