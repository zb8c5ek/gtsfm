"""Customized MVSNet evaluation functions for gtsfm
    reference: https://github.com/FangjinhuaWang/PatchmatchNet

Authors: Ren Liu
"""

import os
import time
from typing import Dict, List, Tuple

import cv2
import numpy as np
from PIL import Image
from plyfile import PlyData, PlyElement
import torch
import torch.nn as nn
import torch.nn.parallel
import torch.backends.cudnn as cudnn
from torch.utils.data import DataLoader

from gtsfm.densify.mvsnets.methods.PatchmatchNet.datasets.gtsfm import MVSDataset
from gtsfm.densify.mvsnets.methods.PatchmatchNet.models.net import PatchmatchNet
from gtsfm.densify.mvsnets.methods.PatchmatchNet.utils import tocuda, tensor2numpy
from gtsfm.densify.mvsnets.methods.PatchmatchNet.datasets.data_io import save_pfm
import gtsfm.utils.logger as logger_utils

logger = logger_utils.get_logger()

cudnn.benchmark = True


def read_img(img: np.ndarray, img_wh: Tuple[int, int]) -> np.ndarray:
    """read image and resize
    Args:
        img: input image
        img_wh: width and height of target size
    Returns:
        np.array image
    """
    # scale 0~255 to 0~1
    np_img = np.array(img, dtype=np.float32) / 255.0
    np_img = cv2.resize(np_img, img_wh, interpolation=cv2.INTER_LINEAR)
    return np_img


def save_mask(filename: str, mask: np.ndarray) -> None:
    """save a binary mask
    Args:
        filename: output file string
        mask: output mask
    """
    assert mask.dtype == np.bool
    mask = mask.astype(np.uint8) * 255
    Image.fromarray(mask).save(filename)


def save_depth_img(filename: str, depth: np.ndarray) -> None:
    """save a depth map as image
    Args:
        filename: output file string
        depth: output depth map
    """
    d = depth.max() - depth.min()
    b = -depth.min()
    depth = 255 * (depth + b) / d
    transform = np.array([d / 255.0, -b])
    depth = depth.astype(np.uint8)

    cv2.imwrite(filename, depth)
    np.save(filename + ".npy", transform)


def parse_pairs(pairs: np.ndarray, nviews: int) -> List[Tuple[int, List[int]]]:
    """prepare candidate pairs for each view from pairwise distances
    Args:
        pairs: pre-calculated pairwise distances
        nviews: number of views, or candidate views as pairs
    Returns:
        For each tuple, is each view's id and list of pair views' indices
    """
    num_viewpoint = pairs.shape[0]

    for i in range(num_viewpoint):
        pairs[i][i] = -np.inf

    pair_idx = np.argsort(pairs, axis=0)[:, 1:nviews][:, ::-1]

    data = []
    for view_idx in range(num_viewpoint):
        ref_view = view_idx
        src_views = pair_idx[i].tolist()
        data.append((ref_view, src_views))
    return data


def reproject_with_depth(
    depth_ref: np.ndarray,
    intrinsics_ref: np.ndarray,
    extrinsics_ref: np.ndarray,
    depth_src: np.ndarray,
    intrinsics_src: np.ndarray,
    extrinsics_src: np.ndarray,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:

    """project the reference point cloud into the source view, then project back
    Args:
        depth_ref: reference depth map
        intrinsics_ref: reference intrinsic
        extrinsics_ref: reference extrinsic
        depth_src: source depth map
        intrinsics_src: intrinsic for source view
        extrinsics_src: extrinsic for source view
    Returns:
        depth_reprojected: reprojected depth map for reference view
        x_reprojected: reprojected x coordinates for reference view
        y_reprojected: reprojected y coordinates for reference view
        x_src: x coordinates for source view
        y_src: y coordinates for source view

    """
    width, height = depth_ref.shape[1], depth_ref.shape[0]
    # step1. project reference pixels to the source view
    # reference view x, y
    x_ref, y_ref = np.meshgrid(np.arange(0, width), np.arange(0, height))
    x_ref, y_ref = x_ref.reshape([-1]), y_ref.reshape([-1])
    # reference 3D space
    xyz_ref = np.matmul(
        np.linalg.inv(intrinsics_ref), np.vstack((x_ref, y_ref, np.ones_like(x_ref))) * depth_ref.reshape([-1])
    )
    # source 3D space
    xyz_src = np.matmul(
        np.matmul(extrinsics_src, np.linalg.inv(extrinsics_ref)), np.vstack((xyz_ref, np.ones_like(x_ref)))
    )[:3]
    # source view x, y
    K_xyz_src = np.matmul(intrinsics_src, xyz_src)
    xy_src = K_xyz_src[:2] / K_xyz_src[2:3]

    # step2. reproject the source view points with source view depth estimation
    # find the depth estimation of the source view
    x_src = xy_src[0].reshape([height, width]).astype(np.float32)
    y_src = xy_src[1].reshape([height, width]).astype(np.float32)
    sampled_depth_src = cv2.remap(depth_src, x_src, y_src, interpolation=cv2.INTER_LINEAR)

    # source 3D space
    # NOTE that we should use sampled source-view depth_here to project back
    xyz_src = np.matmul(
        np.linalg.inv(intrinsics_src), np.vstack((xy_src, np.ones_like(x_ref))) * sampled_depth_src.reshape([-1])
    )
    # reference 3D space
    xyz_reprojected = np.matmul(
        np.matmul(extrinsics_ref, np.linalg.inv(extrinsics_src)), np.vstack((xyz_src, np.ones_like(x_ref)))
    )[:3]
    # source view x, y, depth
    depth_reprojected = xyz_reprojected[2].reshape([height, width]).astype(np.float32)
    K_xyz_reprojected = np.matmul(intrinsics_ref, xyz_reprojected)
    xy_reprojected = K_xyz_reprojected[:2] / K_xyz_reprojected[2:3]
    x_reprojected = xy_reprojected[0].reshape([height, width]).astype(np.float32)
    y_reprojected = xy_reprojected[1].reshape([height, width]).astype(np.float32)

    return depth_reprojected, x_reprojected, y_reprojected, x_src, y_src


def check_geometric_consistency(
    depth_ref: np.ndarray,
    intrinsics_ref: np.ndarray,
    extrinsics_ref: np.ndarray,
    depth_src: np.ndarray,
    intrinsics_src: np.ndarray,
    extrinsics_src: np.ndarray,
    geo_pixel_thres: float,
    geo_depth_thres: float,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """check geometric consistency and return valid points
    Args:
        depth_ref: reference depth map
        intrinsics_ref: refenrece intrinsic
        extrinsics_ref: refenrece extrinsic
        depth_src: source depth map
        intrinsics_src: source intrinsic
        extrinsics_src: source extrinsic
        geo_pixel_thres: geometric pixel threshold
        geo_depth_thres: geometric depth threshold
    Returns:
        mask: mask for points with geometric consistency
        depth_reprojected: reprojected reference depth map
        x2d_src: source x coodinates for points on the 2D plane
        y2d_src: source y coodinates for points on the 2D plane
    """
    width, height = depth_ref.shape[1], depth_ref.shape[0]
    x_ref, y_ref = np.meshgrid(np.arange(0, width), np.arange(0, height))
    depth_reprojected, x2d_reprojected, y2d_reprojected, x2d_src, y2d_src = reproject_with_depth(
        depth_ref, intrinsics_ref, extrinsics_ref, depth_src, intrinsics_src, extrinsics_src
    )

    # check |p_reproj-p_1| < 1
    dist = np.sqrt((x2d_reprojected - x_ref) ** 2 + (y2d_reprojected - y_ref) ** 2)

    # check |d_reproj-d_1| / d_1 < 0.01
    # depth_ref = np.squeeze(depth_ref, 2)
    depth_diff = np.abs(depth_reprojected - depth_ref)
    relative_depth_diff = depth_diff / depth_ref

    mask = np.logical_and(dist < geo_pixel_thres, relative_depth_diff < geo_depth_thres)
    depth_reprojected[~mask] = 0

    return mask, depth_reprojected, x2d_src, y2d_src


def filter_depth(
    data: np.ndarray,
    nviews: int,
    depth_list: List[np.ndarray],
    confidence_list: List[np.ndarray],
    geo_pixel_thres: float,
    geo_depth_thres: float,
    photo_thres: float,
    img_wh: Tuple[int, int],
    out_folder: str,
    plyfilename: str,
    save_output: bool = False,
) -> np.ndarray:
    """filter depthmap and output valid dense point cloud
    Args:
        data: mvsnet data with images and camera parameters
        nviews: number of views
        depth_list: list of depth maps for each view
        confidence_list: list of confidence map for each view
        geo_pixel_thres: geometric pixel threshold
        geo_depth_thres: geometric depth threshold
        photo_thres: photographic threshold
        img_wh: tuple of image size
        out_folder: output folder path string
        plyfilename: output polygon mesh path string
        save_output: whether to save output files
    Returns:
        dense point cloud: [N, 3] np.ndarray
    """
    # for the final point cloud
    vertexs = []
    vertex_colors = []

    pair_data = parse_pairs(data["pairs"], nviews)

    # for each reference view and the corresponding source views
    for ref_view, src_views in pair_data:
        # load the camera parameters
        ref_intrinsics, ref_extrinsics = data["cameras"][ref_view]

        # load the reference image
        ref_img = read_img(data["images"][ref_view], img_wh)
        # load the estimated depth of the reference view
        ref_depth_est = depth_list[ref_view][0]
        # load the photometric mask of the reference view
        confidence = confidence_list[ref_view]

        if save_output:
            os.makedirs(os.path.join(out_folder, "depth_img"), exist_ok=True)
            save_depth_img(
                os.path.join(out_folder, "depth_img/depth_{:0>8}.png".format(ref_view)),
                ref_depth_est.astype(np.float32),
            )
            save_depth_img(
                os.path.join(out_folder, "depth_img/conf_{:0>8}.png".format(ref_view)), confidence.astype(np.float32)
            )

        photo_mask = confidence > photo_thres

        all_srcview_depth_ests = []

        # compute the geometric mask
        geo_mask_sum = 0
        for src_view in src_views:
            # camera parameters of the source view
            src_intrinsics, src_extrinsics = data["cameras"][src_view]

            # the estimated depth of the source view
            src_depth_est = depth_list[src_view][0]

            geo_mask, depth_reprojected, x2d_src, y2d_src = check_geometric_consistency(
                ref_depth_est,
                ref_intrinsics,
                ref_extrinsics,
                src_depth_est,
                src_intrinsics,
                src_extrinsics,
                geo_pixel_thres,
                geo_depth_thres,
            )
            geo_mask_sum += geo_mask.astype(np.int32)
            all_srcview_depth_ests.append(depth_reprojected)

        depth_est_averaged = (sum(all_srcview_depth_ests) + ref_depth_est) / (geo_mask_sum + 1)
        # at least 3 source views matched
        # large threshold, high accuracy, low completeness
        geo_mask = geo_mask_sum >= 3
        final_mask = np.logical_and(photo_mask, geo_mask)

        if save_output:
            os.makedirs(os.path.join(out_folder, "mask"), exist_ok=True)
            save_mask(os.path.join(out_folder, "mask/{:0>8}_photo.png".format(ref_view)), photo_mask)
            save_mask(os.path.join(out_folder, "mask/{:0>8}_geo.png".format(ref_view)), geo_mask)
            save_mask(os.path.join(out_folder, "mask/{:0>8}_final.png".format(ref_view)), final_mask)

        logger.info(
            "[Densify::PatchMatchNet] processing view:{:0>2}, geo_mask:{:3f} photo_mask:{:3f} final_mask:{:3f} ".format(
                ref_view, geo_mask.mean(), photo_mask.mean(), final_mask.mean()
            )
        )

        height, width = depth_est_averaged.shape[:2]
        x, y = np.meshgrid(np.arange(0, width), np.arange(0, height))

        valid_points = final_mask
        x, y, depth = x[valid_points], y[valid_points], depth_est_averaged[valid_points]

        color = ref_img[valid_points]
        xyz_ref = np.matmul(np.linalg.inv(ref_intrinsics), np.vstack((x, y, np.ones_like(x))) * depth)
        xyz_world = np.matmul(np.linalg.inv(ref_extrinsics), np.vstack((xyz_ref, np.ones_like(x))))[:3]
        vertexs.append(xyz_world.transpose((1, 0)))
        vertex_colors.append((color * 255).astype(np.uint8))

    vertexs_raw = np.concatenate(vertexs, axis=0)

    if save_output:
        vertex_colors = np.concatenate(vertex_colors, axis=0)
        vertexs = np.array([tuple(v) for v in vertexs_raw], dtype=[("x", "f4"), ("y", "f4"), ("z", "f4")])
        vertex_colors = np.array(
            [tuple(v) for v in vertex_colors], dtype=[("red", "u1"), ("green", "u1"), ("blue", "u1")]
        )

        vertex_all = np.empty(len(vertexs), vertexs.dtype.descr + vertex_colors.dtype.descr)
        for prop in vertexs.dtype.names:
            vertex_all[prop] = vertexs[prop]
        for prop in vertex_colors.dtype.names:
            vertex_all[prop] = vertex_colors[prop]

        el = PlyElement.describe(vertex_all, "vertex")
        PlyData([el]).write(plyfilename)
        logger.info("[Densify::PatchMatchNet] saving the final model to", plyfilename)

    return vertexs_raw


def eval_function(gtargs: Dict) -> np.ndarray:
    """top wraping of evaluation function
    Args:
        gtargs: python dictionary mvsnet data generated by GTSFM
    Returns:
        dense point cloud: [N, 3] np.ndarray
    """

    data = gtargs["mvsnetsData"]
    save_output = gtargs["save_output"]
    """
        data.images
        data.cameras [[i1, e1], [i2, e2], ..., [in, en]]
        data.pairs   
        data.depthRange
    """
    test_dataset = MVSDataset(data, gtargs["n_views"], img_wh=gtargs["img_wh"])
    TestImgLoader = DataLoader(test_dataset, 1, shuffle=False, num_workers=4, drop_last=False)
    # model
    model = PatchmatchNet(
        patchmatch_interval_scale=[0.005, 0.0125, 0.025],
        propagation_range=[6, 4, 2],
        patchmatch_iteration=[1, 2, 2],
        patchmatch_num_sample=[8, 8, 16],
        propagate_neighbors=[0, 8, 16],
        evaluate_neighbors=[9, 9, 9],
    )
    model = nn.DataParallel(model)
    model.cuda()

    # load checkpoint file specified by args.loadckpt
    logger.info("[Densify::PatchMatchNet] loading model {}".format(gtargs["loadckpt"]))
    state_dict = torch.load(gtargs["loadckpt"])
    model.load_state_dict(state_dict["model"])
    model.eval()
    depth_est_list = {}
    confidence_est_list = {}
    with torch.no_grad():
        for batch_idx, sample in enumerate(TestImgLoader):
            start_time = time.time()
            sample_cuda = tocuda(sample)
            outputs = model(
                sample_cuda["imgs"], sample_cuda["proj_matrices"], sample_cuda["depth_min"], sample_cuda["depth_max"]
            )

            outputs = tensor2numpy(outputs)
            del sample_cuda
            logger.info(
                "[Densify::PatchMatchNet] Iter {}/{}, time = {:.3f}".format(
                    batch_idx, len(TestImgLoader), time.time() - start_time
                )
            )
            filenames = sample["filename"]
            ids = sample["idx"]

            # save depth maps and confidence maps
            for idx, filename, depth_est, photometric_confidence in zip(
                ids, filenames, outputs["refined_depth"]["stage_0"], outputs["photometric_confidence"]
            ):

                idx = idx.cpu().numpy().tolist()
                depth_est_list[idx] = depth_est.copy()
                confidence_est_list[idx] = photometric_confidence.copy()

                if save_output:
                    depth_filename = os.path.join(gtargs["outdir"], filename.format("depth_est", ".pfm"))
                    confidence_filename = os.path.join(gtargs["outdir"], filename.format("confidence", ".pfm"))

                    os.makedirs(depth_filename.rsplit("/", 1)[0], exist_ok=True)
                    os.makedirs(confidence_filename.rsplit("/", 1)[0], exist_ok=True)
                    # save depth maps
                    depth_est = np.squeeze(depth_est, 0)
                    save_pfm(depth_filename, depth_est)
                    # save confidence maps
                    save_pfm(confidence_filename, photometric_confidence)

    out_folder = gtargs["outdir"]

    # step2. filter saved depth maps with geometric constraints
    return filter_depth(
        data,
        gtargs["n_views"],
        depth_est_list,
        confidence_est_list,
        gtargs["thres"][0],
        gtargs["thres"][1],
        gtargs["thres"][2],
        gtargs["img_wh"],
        out_folder,
        os.path.join(gtargs["outdir"], "patchmatchnet_l3.ply"),
        save_output,
    )
