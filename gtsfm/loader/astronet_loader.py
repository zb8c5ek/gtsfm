"""Simple loader class that reads a dataset with metadata formatted in the COLMAP style.

Authors: Travis Driver
"""

import os
from pathlib import Path
from typing import Optional, Dict, Tuple, List

import numpy as np
from gtsam import Cal3Bundler, Pose3, Rot3, Point3, SfmTrack
import trimesh
import pyvista as pv

import gtsfm.utils.images as img_utils
import gtsfm.utils.io as io_utils
import gtsfm.utils.logger as logger_utils
from gtsfm.common.image import Image
from gtsfm.loader.loader_base import LoaderBase

# from colmap.scripts.python.read_write_model import read_model
from gtsfm.utils.read_write_model import read_model
from gtsfm.utils.read_write_model import Camera as ColmapCamera
from gtsfm.utils.read_write_model import Image as ColmapImage
from gtsfm.utils.read_write_model import Point3D as ColmapPoint3D


logger = logger_utils.get_logger()


class AstroNetLoader(LoaderBase):
    """Simple loader class that reads a dataset with ground-truth files and dataset meta-information
    formatted in the COLMAP style. This meta-information may include image file names stored
    in a images.txt file, or ground truth camera poses for each image/frame. Images should be
    present in the specified image directory.

    Note: assumes all images are of the same dimensions.
    """

    def __init__(
        self,
        data_dir: str,
        gt_scene_mesh_path: str = None,
        use_gt_intrinsics: bool = True,
        use_gt_extrinsics: bool = True,
        max_frame_lookahead: int = 2,
        max_resolution: int = 1024,
    ) -> None:
        """Initialize loader from a specified segment directory (data_dir) on disk.

        <data_dir>/
             ├── images/: distorted grayscale images
             ├── cameras.bin: camera calibrations (see https://colmap.github.io/format.html#cameras-txt)
             ├── images.bin: 3D poses and 2D tracks (see https://colmap.github.io/format.html#images-txt)
             └── points3D.bin: 3D tracks (see https://colmap.github.io/format.html#points3d-txt)


        Args:
            data_dir: path to directory containing the COLMAP-formatted data: cameras.bin, images.bin, and points3D.bin
            gt_scene_mesh_path (optional): path to Alias Wavefront Object (.obj) file of target small body
            use_gt_intrinsics: whether to use ground truth intrinsics. If calibration is
               not found on disk, then use_gt_intrinsics will be set to false automatically.
            use_gt_extrinsics (optional): whether to use ground truth extrinsics
            max_frame_lookahead (optional): maximum number of consecutive frames to consider for
                matching/co-visibility. Any value of max_frame_lookahead less than the size of
                the dataset assumes data is sequentially captured
            max_resolution (optional): integer representing maximum length of image's short side
               e.g. for 1080p (1920 x 1080), max_resolution would be 1080

        Raises:
            FileNotFoundError if image path does not exist

        """
        self._use_gt_intrinsics = use_gt_intrinsics
        self._use_gt_extrinsics = use_gt_extrinsics
        self._max_frame_lookahead = max_frame_lookahead
        self._max_resolution = max_resolution

        # Use COLMAP model reader to load data and convert to GTSfM format
        if Path(data_dir).exists():
            _cameras, _images, _points = read_model(path=data_dir, ext=".bin")
            self._calibrations, self._wTi_list, img_fnames, self._sfmtracks = self.colmap2gtsfm(
                _cameras, _images, _points
            )

        if gt_scene_mesh_path is not None and Path(gt_scene_mesh_path).exists():
            _vertices, _faces = self.load_obj(gt_scene_mesh_path)
            self._gt_scene_trimesh = trimesh.Trimesh(_vertices, _faces)

        if self._calibrations is None:
            self._use_gt_intrinsics = False

        if self._wTi_list is None:
            self._use_gt_extrinsics = False

        # preserve ordering of images
        self._image_paths = []
        for img_fname in img_fnames:
            img_fpath = os.path.join(data_dir, "images", img_fname)
            if not Path(img_fpath).exists():
                raise FileNotFoundError(f'Could not locate image at {img_fpath}.')
            self._image_paths.append(img_fpath)

        self._num_imgs = len(self._image_paths)
        logger.info("AstroNet loader found and loaded %d images", self._num_imgs)

    @staticmethod
    def colmap2gtsfm(
        cameras: Dict[int, ColmapCamera],
        images: Dict[int, ColmapImage],
        points3D: Dict[int, ColmapPoint3D],
        return_tracks: Optional[bool] = False,
    ) -> Tuple[List[Cal3Bundler], List[Pose3], List[str], List[Point3]]:
        """Converts COLMAP-formatted variables to GTSfM format

        Args:
            cameras: dictionary of COLMAP-formatted Cameras
            images: dictionary of COLMAP-formatted Images
            points3D: dictionary of COLMAP-formatted Point3Ds
            return_tracks (optional): whether or not to return tracks

        Returns:
            cameras_gtsfm: list of N camera calibrations corresponding to the N images in images_gtsfm
            images_gtsfm: list of N camera poses when each image was taken
            img_fnames: file names of images in images_gtsfm
            tracks3D_gtsfm: tracks of points in points3D

        """
        cameras_gtsfm, images_gtsfm, img_fnames, tracks3D_gtsfm = None, None, None, None

        # Assumes input cameras use OPENCV_FULL model
        # TODO: don't use Cal3Bundler
        if len(images) > 0 and len(cameras) > 0:
            cameras_gtsfm, images_gtsfm, img_fnames = [], [], []
            for img in images.values():
                images_gtsfm.append(Pose3(Rot3(img.qvec2rotmat()), img.tvec).inverse())
                img_fnames.append(img.name)
                fx, fy, cx, cy, k1, k2, p1, p2, k3, k4, k5, k6 = cameras[img.camera_id].params
                cameras_gtsfm.append(Cal3Bundler(fx, k1, k2, cx, cy))

        if len(points3D) > 0 and return_tracks:
            tracks3D_gtsfm = []
            for point3D in points3D.values():
                track3D = SfmTrack(point3D.xyz)
                for (image_id, point2d_idx) in zip(point3D.image_ids, point3D.point2D_idxs):
                    track3D.add_measurement(image_id, images[image_id].xys[point2d_idx])
                tracks3D_gtsfm.append(track3D)

        return cameras_gtsfm, images_gtsfm, img_fnames, tracks3D_gtsfm

    @staticmethod
    def load_obj(fn: str) -> Tuple[np.ndarray, np.ndarray]:
        """Reads in Alias Wavefront Object (.obj) file

        Args:
            fn: path to .obj file

        Returns:
            vertices: (V, 3) array of mesh vertices
            faces: (F, 3) array of mesh faces

        Raises:
            ValueError if no information found at fn
        """
        # TODO: don't rely on PyVista
        polydata = pv.read(fn)

        # If there are no points in 'vtkPolyData' something went wrong
        if polydata.GetNumberOfPoints() == 0:
            raise ValueError("No point data could be loaded from '" + fn + "'")

        # Extract vertex and face arrays
        vertices = polydata.points  # (V, 3)
        faces = polydata.faces.reshape(-1, 4)[:, 1:]  # (F, 3)

        return vertices, faces

    def __len__(self) -> int:
        """The number of images in the dataset.

        Returns:
            the number of images.
        """
        return self._num_imgs

    def get_image(self, index: int) -> Image:
        """Get the image at the given index.

        Args:
            index: the index to fetch.

        Raises:
            IndexError: if an out-of-bounds image index is requested.

        Returns:
            Image: the image at the query index.
        """
        if index < 0 or index >= len(self):
            raise IndexError("Image index is invalid")

        img = io_utils.load_image(self._image_paths[index])
        return img

    def get_camera_intrinsics(self, index: int) -> Cal3Bundler:
        """Get the camera intrinsics at the given index.

        Args:
            the index to fetch.

        Returns:
            intrinsics for the given camera.
        """
        if index < 0 or index >= len(self):
            raise IndexError("Image index is invalid")

        if self._use_gt_intrinsics:
            intrinsics = self._calibrations[index]
            logger.info("Loading ground truth calibration.")

        return intrinsics

    def get_camera_pose(self, index: int) -> Optional[Pose3]:
        """Get the camera pose (in world coordinates) at the given index.

        Args:
            index: the index to fetch.

        Returns:
            the camera pose w_T_index.
        """
        if index < 0 or index >= len(self):
            raise IndexError("Image index is invalid")

        if not self._use_gt_extrinsics:
            return None

        wTi = self._wTi_list[index]
        return wTi

    def is_valid_pair(self, idx1: int, idx2: int) -> bool:
        """Checks if (idx1, idx2) is a valid pair.

        Args:
            idx1: first index of the pair.
            idx2: second index of the pair.

        Returns:
            validation result.
        """
        return idx1 < idx2 and abs(idx1 - idx2) <= self._max_frame_lookahead
