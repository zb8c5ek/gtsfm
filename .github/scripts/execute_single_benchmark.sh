#!/bin/bash

DATASET_NAME=$1
CONFIG_NAME=$2
MAX_FRAME_LOOKAHEAD=$3
IMAGE_EXTENSION=$4
LOADER_NAME=$5
MAX_RESOLUTION=$6
SHARE_INTRINSICS=$7

# Extract the data, configure arguments for runner.
if [ "$DATASET_NAME" == "door-12" ]; then
  DATASET_ROOT=tests/data/set1_lund_door

elif [ "$DATASET_NAME" == "palace-fine-arts-281" ]; then
  DATASET_ROOT="palace-fine-arts-281"

elif [ "$DATASET_NAME" == "2011205_rc3" ]; then
  DATASET_ROOT="2011205_rc3"
fi

echo "Config: ${CONFIG_NAME}, Loader: ${LOADER_NAME}"
echo "Max. Frame Lookahead: ${MAX_FRAME_LOOKAHEAD}, Image Extension: ${IMAGE_EXTENSION}, Max. Resolution: ${MAX_RESOLUTION}"
echo "Share intrinsics for all images? ${SHARE_INTRINSICS}"

# Setup the command line arg if intrinsics are to be shared
if [ "$SHARE_INTRINSICS" == "true" ]; then
  export SHARE_INTRINSICS_ARG="--share_intrinsics"
else
  export SHARE_INTRINSICS_ARG=""
fi

echo "Share intrinsics CLI argument: ${SHARE_INTRINSICS_ARG}"

# Run GTSFM on the dataset.
if [ "$LOADER_NAME" == "olsson-loader" ]; then
  python gtsfm/runner/run_scene_optimizer_olssonloader.py \
    --dataset_root $DATASET_ROOT \
    --max_frame_lookahead $MAX_FRAME_LOOKAHEAD \
    --config_name ${CONFIG_NAME}.yaml \
    --image_extension $IMAGE_EXTENSION \
    --max_resolution ${MAX_RESOLUTION} \
    ${SHARE_INTRINSICS_ARG}

elif [ "$LOADER_NAME" == "colmap-loader" ]; then
  python gtsfm/runner/run_scene_optimizer_colmaploader.py \
    --images_dir ${IMAGES_DIR} \
    --colmap_files_dirpath $COLMAP_FILES_DIRPATH \
    --max_frame_lookahead $MAX_FRAME_LOOKAHEAD \
    --config_name ${CONFIG_NAME}.yaml \
    --max_resolution ${MAX_RESOLUTION} \
    ${SHARE_INTRINSICS_ARG}

elif [ "$LOADER_NAME" == "astronet" ]; then
  python gtsfm/runner/run_scene_optimizer_astronet.py \
    --data_dir $DATASET_ROOT \
    --max_frame_lookahead $MAX_FRAME_LOOKAHEAD \
    --config_name ${CONFIG_NAME}.yaml \
    --max_resolution ${MAX_RESOLUTION} \
    ${SHARE_INTRINSICS_ARG}
fi
