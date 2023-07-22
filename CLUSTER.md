# GTSFM on CLUSTER

### How to Run GTSfM on a Cluster?

GTSfM uses the [SSHCluster](https://docs.dask.org/en/stable/deploying-ssh.html#dask.distributed.SSHCluster) module of [Dask](https://distributed.dask.org/en/stable/) to provide cluster-utilization functionality for SfM execution. This readme is a step-by-step guide on how to set up your machines for a successful run on a cluster.

1. Choose which machine will serve as the scheduler. The data only needs to be on the scheduler node.
2. Enable passwordless SSH between all the workers on the cluster
    - Log in into a machine
    - For each of the other workers on the cluster run
        * ```bash 
          ssh-copy-id username@machine_ip_address_of_another_worker
          ```
        * Repeat the above two steps on all machines
    - Note machines should be able to ssh into themselves passwordless e.g. host1 should be able to ssh into host1.
3. Clone gtsfm and follow the main readme file to setup the environment on all nodes in the cluster at an identical path
    - ```bash
        git clone https://github.com/borglab/gtsfm.git
        conda env create -f environment_linux.yml
        conda activate gtsfm-v1
      ```
4. Log into scheduler again and download the data to scheduler machine
5. Create a config file listing the cluster workers (example in [gtsfm/configs/cluster.yaml](https://github.com/borglab/gtsfm/blob/master/gtsfm/configs/cluster.yaml))
6. Run gtsfm with –cluster_config flag enabled, for example
    - ```
      python /home/username/gtsfm/gtsfm/runner run_scene_optimizer_colmaploader.py --images_dir /home/username/gtsfm/skydio-32/images/ --config_name sift_front_end.yaml --colmap_files_dirpath /home/hstepanyan3/gtsfm/skydio-32/colmap_crane_mast_32imgs/ --cluster_config cluster.yaml
      ```
    - Note that the first worker in the cluster.yaml file must be the scheduler machine where the data is hosted.
    - Always provide absolute paths for all directories
7. If you would like to check out the dask dashboard, you will need to do port forwarding from machine to your local computer:
    - ```
      ssh -N -f -L localhost:local_port:localhost:machine_port username@machine_adress
      ```

8. The results will be generated on the scheduler machine. If you would like to download results from the scheduler machine to your local computer:
    - ```
      scp -r username@host:machine/results/path /local/computer/directory
      ```