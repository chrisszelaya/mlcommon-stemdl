#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# slstr_cloud.py

# SciML-Bench
# Copyright © 2022 Scientific Machine Learning Research Group
# Scientific Computing Department, Rutherford Appleton Laboratory
# Science and Technology Facilities Council, UK.
# All rights reserved.

import sys
sys.path.append("..")

import yaml, os, atexit, h5py, sys, time, decimal, argparse
import tensorflow as tf
from data_loader import load_datasets
from model import unet
from pathlib import Path
import numpy as np
from data_loader import SLSTRDataLoader
from cloudmesh.common.StopWatch import StopWatch
from cloudmesh.common.variables import Variables

# MLCommons logging
from mlperf_logging import mllog
import logging

cm_vars = Variables()
currentgpu = cm_vars['currentgpu']
currentepoch = cm_vars['currentepoch']

# Loss function
def weighted_cross_entropy(beta):
    """
    Weighted Binary Cross Entropy implementation
    :param beta: beta weight to adjust relative importance of +/- label
    :return: weighted BCE loss
    """
    def convert_to_logits(y_pred):
        # see https://github.com/tensorflow/tensorflow/blob/r1.10/tensorflow/python/keras/backend.py#L3525
        y_pred = tf.clip_by_value(
            y_pred, tf.keras.backend.epsilon(), 1 - tf.keras.backend.epsilon())

        return tf.math.log(y_pred / (1 - y_pred))

    def loss(y_true, y_pred):
        y_pred = convert_to_logits(y_pred)
        loss = tf.nn.weighted_cross_entropy_with_logits(
            logits=y_pred, labels=y_true, pos_weight=beta)

        # or reduce_sum and/or axis=-1
        return tf.reduce_mean(loss)

    return loss

def reconstruct_from_patches(args, patches: tf.Tensor, nx: int, ny: int, patch_size: int) -> tf.Tensor:
    """Reconstruct a full image from a series of patches

    :param patches: array with shape (num patches, height, width)
    :param nx: the number of patches in the x direction
    :param ny: the number of patches in the y direction
    :param patch_size: the size of th patches
    :return: the reconstructed image with shape (1, height, weight, 1)
    """
    # Read arguments 
    IMAGE_H = args['IMAGE_H']
    IMAGE_W = args['IMAGE_W']

    h = ny * patch_size
    w = nx * patch_size
    reconstructed = np.zeros((1, h, w, 1))

    for i in range(ny):
        for j in range(nx):
            py = i * patch_size
            px = j * patch_size
            reconstructed[0, py:py + patch_size, px:px + patch_size] = patches[0, i, j]

    # Crop off the additional padding
    offset_y = (h - IMAGE_H) // 2
    offset_x = (w - IMAGE_W) // 2
    reconstructed = tf.image.crop_to_bounding_box(reconstructed, offset_y, offset_x, IMAGE_H, IMAGE_W)
    return reconstructed


# Inference
def cloud_inference(args)-> None:
    print('Running benchmark slstr_cloud in inference mode.')
    # Read arguments 
    CROP_SIZE = args['CROP_SIZE']
    PATCH_SIZE = args['PATCH_SIZE']
    N_CHANNELS = args['N_CHANNELS']

    # Load model
    modelPath = os.path.expanduser(args['model_file'])    
    model = tf.keras.models.load_model(modelPath)

    # Read inference files
    inference_dir = os.path.expanduser(args['inference_dir'])
    file_paths = list(Path(inference_dir).glob('**/S3A*.hdf'))

    # Create data loader in single image mode. This turns off shuffling and
    # only yields batches of images for a single image at a time so they can be
    # reconstructed.
    data_loader = SLSTRDataLoader(args, file_paths, single_image=True, crop_size=CROP_SIZE)
    dataset = data_loader.to_dataset()
    
    # Inference Loop
    for patches, file_name in dataset:
        file_name = Path(file_name.numpy().decode('utf-8'))
        #print(f"Processing file {file_name}")

        # convert patches to a batch of patches
        n, ny, nx, _ = patches.shape
        patches = tf.reshape(patches, (n * nx * ny, PATCH_SIZE, PATCH_SIZE, N_CHANNELS))

        # perform inference on patches
        mask_patches = model.predict_on_batch(patches)

        # crop edge artifacts
        mask_patches = tf.image.crop_to_bounding_box(mask_patches, CROP_SIZE // 2, CROP_SIZE // 2, PATCH_SIZE - CROP_SIZE, PATCH_SIZE - CROP_SIZE)

        # reconstruct patches back to full size image
        mask_patches = tf.reshape(mask_patches, (n, ny, nx, PATCH_SIZE - CROP_SIZE, PATCH_SIZE - CROP_SIZE, 1))
        mask = reconstruct_from_patches(args, mask_patches, nx, ny, patch_size=PATCH_SIZE - CROP_SIZE)
        output_dir = os.path.expanduser(args['output_dir'])
        mask_name = f"{output_dir}/{file_name.name}.h5"
        # print('mask_name: ', mask_name)

        with h5py.File(mask_name, 'w') as handle:
            handle.create_dataset('mask', data=mask)
    # Return the number of inferences
    return len(file_paths)

#####################################################################
# Training mode                                                     #
#####################################################################

def cloud_training(args)-> None:  
    print('Running benchmark slstr_cloud in training mode.')   
    tf.random.set_seed(args['seed'])
    data_dir = os.path.expanduser(args['train_dir'])

    # load the datasets
    train_dataset, test_dataset  = load_datasets(dataset_dir=data_dir, config=args)
    
    samples = list(Path(data_dir).glob('**/S3A*.hdf'))
    num_samples = len(samples)
    print("num_samples: ", num_samples)

    # Running training on multiple GPUs
    mirrored_strategy = tf.distribute.MirroredStrategy()
    optimizer = tf.keras.optimizers.Adam(args['learning_rate'])
    
    with mirrored_strategy.scope():
        # create U-Net model
        model = unet(input_shape=(args['PATCH_SIZE'], args['PATCH_SIZE'], args['N_CHANNELS']))
        model.compile(optimizer=optimizer, loss='binary_crossentropy', metrics=['accuracy'])
        history = model.fit(train_dataset, validation_data=test_dataset, epochs=args['epochs'], verbose=1)

    # Close file descriptors
    #atexit.register(mirrored_strategy._extended._collective_ops._pool.close)

    # save model
    modelPath = os.path.expanduser(args['model_file'])
    tf.keras.models.save_model(model, modelPath)
    print('END slstr_cloud in training mode.')
    return num_samples

### Main
# Running the benchmark: python slstr_cloud.py --config ./config.yaml
def main():
    StopWatch.start("total")

    # Read command line arguments
    parser = argparse.ArgumentParser(description='CloudMask command line arguments',\
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)

    parser.add_argument('--config', default=os.path.expanduser('./config.yaml'), help='path to config file')
    command_line_args = parser.parse_args()

    configFile = os.path.expanduser(command_line_args.config)

    # Read YAML file
    with open(configFile, 'r') as stream:
        args = yaml.safe_load(stream)
    log_file = os.path.expanduser(args['log_file'])

    # MLCommons logging
    mlperf_logfile = os.path.expanduser(args['mlperf_logfile'])
    mllog.config(filename=mlperf_logfile)
    mllogger = mllog.get_mllogger()
    logger = logging.getLogger(__name__)

     # Values extracted from config.yaml
    mllogger.event(key=mllog.constants.SUBMISSION_BENCHMARK, value=args['benchmark'])
    mllogger.event(key=mllog.constants.SUBMISSION_ORG, value=args['organisation'])
    mllogger.event(key=mllog.constants.SUBMISSION_DIVISION, value=args['division'])
    mllogger.event(key=mllog.constants.SUBMISSION_STATUS, value=args['status'])
    mllogger.event(key=mllog.constants.SUBMISSION_PLATFORM, value=args['platform']) 
    mllogger.start(key=mllog.constants.INIT_START)

    mllogger.event(key='number_of_ranks', value=args['gpu']) 
    mllogger.event(key='number_of_nodes', value=args['nodes'])
    mllogger.event(key='accelerators_per_node', value=args['accelerators_per_node']) 
    mllogger.end(key=mllog.constants.INIT_STOP)
    
    # Training
    StopWatch.start("training")
    start = time.time()
    mllogger.event(key=mllog.constants.EVAL_START, value="Start: Taining")
    samples = cloud_training(args)
    mllogger.event(key=mllog.constants.EVAL_STOP, value="Stop: Training")
    diff = time.time() - start
    StopWatch.stop("training")
    elapsedTime = decimal.Decimal(diff)
    time_per_epoch = elapsedTime/int(args['epochs'])
    time_per_epoch_str = f"{time_per_epoch:.2f}"
    with open(log_file, "a") as logfile:
        logfile.write(f"CloudMask training, samples = {samples}, epochs={args['epochs']}, bs={args['batch_size']}, nodes={args['nodes']}, gpus={args['gpu']}, time_per_epoch={time_per_epoch_str}\n")

    # Inference
    StopWatch.start("inference")
    start = time.time()
    mllogger.event(key=mllog.constants.EVAL_START, value="Start: Inference")
    number_inferences = cloud_inference(args)
    mllogger.event(key=mllog.constants.EVAL_STOP, value="Stop: Inference")
    diff = time.time() - start
    StopWatch.stop("inference")
    elapsedTime = decimal.Decimal(diff)
    time_per_inference = elapsedTime/number_inferences
    time_per_inference_str = f"{time_per_inference:.2f}"
    print("number_inferences: ", number_inferences)
    with open(log_file, "a") as logfile:
        logfile.write(f"CloudMask inference, inferences={number_inferences}, bs={args['batch_size']}, nodes={args['nodes']}, gpus={args['gpu']}, time_per_inference={time_per_inference_str}\n")
    mllogger.end(key=mllog.constants.RUN_STOP, value="CloudMask benchmark run finished", metadata={'status': 'success'})
    StopWatch.stop("total")
    StopWatch.benchmark(filename=f'slstr_stopwatch_benchmark_{currentgpu}_{currentepoch}.log', tag=f'{currentgpu}_{currentepoch}')

if __name__ == "__main__":
    main()

