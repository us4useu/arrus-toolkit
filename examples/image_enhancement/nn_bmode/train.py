# File:       nn_bmode/nn_bmode/train.py
# Author:     Dongwoon Hyun (dongwoon.hyun@stanford.edu)
# Created on: 2019-08-30
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import tensorflow as tf
import time
import nn_bmode.losses as losses
from nn_bmode.utils import load_from_mat, TensorBoardBmode
from nn_bmode.model import NNBmode
import scipy.io as sio
import argparse


DATADIR = "../data"
SAVEDIR = "../runs"


def train(run_dir=None, data_dir=None):
    # Directory to save TensorBoard results
    if run_dir is None:
        run_dir = f"{SAVEDIR}/{time.strftime('%Y%m%d_%H%M')}"
    if data_dir is None:
        data_dir = DATADIR
    print(f"Processing run {run_dir}...")
    # Network specifications
    depth = 8  # 8 layers of conv2d, relu, batchnorm
    nfilters = 32  # 32 filters per layer
    ksizes = 3  # Kernel size of 3x3 in all layers

    # Hyperparameters
    lr = 1e-3
    batch_size = 128
    n_epochs = 50  # Total of 50 passes through training set
    reg1 = 0  # Skip L1 regularization to filter weights
    reg2 = 1e-3  # Apply L2 regularization to filter weights

    config = tf.compat.v1.ConfigProto()
    config.gpu_options.allow_growth = True
    session = tf.compat.v1.Session(config=config)

    # Load data
    trn_dataset = f"{data_dir}/train_64x64x16x5000.mat"
    val_dataset = f"{data_dir}/valid_64x64x16x128.mat"
    x_trn, y_trn = load_from_mat(trn_dataset)
    x_val, y_val = load_from_mat(val_dataset)

    # Define custom loss function
    def custom_loss(y, yhat):
        l1loss = losses.get_l1loss_wopt_log(y, yhat)
        msssim = losses.get_msssim_wopt_log(y, yhat)
        msloss = 1 - msssim
        # Equalize contributions of loss heuristically
        msloss_multiplier = 200
        return l1loss + msloss * msloss_multiplier

    # Create neural network beamformer model
    model = NNBmode(x_trn[0].shape,
                   n_layers=depth, n_filters=nfilters, kernel_size=ksizes,
                   optimizer=tf.keras.optimizers.Adam(lr),
                   loss=custom_loss,
                   metrics=[
                       losses.get_l1loss_wopt_log,
                       losses.get_l2loss_wopt_log,
                       losses.get_msssim_wopt_log,
                   ])

    # Create TensorBoard outputs
    # img_idx = [1, 34, 91]  # Choose 3 images to display from validation set
    # tbCallBack = TensorBoardBmode(
    #     val_data=(x_val[img_idx], y_val[img_idx]),
    #     log_dir=run_dir,
    #     histogram_freq=0,
    #     write_graph=True,
    #     write_images=False,
    #     profile_batch=0,
    # )

    # Execute training
    results = model.fit(
        x=x_trn,
        y=y_trn,
        batch_size=batch_size,
        epochs=n_epochs,
        validation_data=(x_val, y_val),
        shuffle=True,
        # callbacks=[tbCallBack],
    )

    # Save result history plus hyperparameters
    results.history.update(
        {
            "nfilt": nfilters,
            "lr": lr,
            "batch_size": batch_size,
            "n_epochs": n_epochs,
            "reg1": reg1,
            "reg2": reg2,
        }
    )
    sio.savemat(f"{run_dir}/results.mat", results.history)
    # Save model
    model.save_weights(f"{run_dir}/model.h5", save_format="h5")
    # Clear graph
    tf.keras.backend.clear_session()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Builds NNBmode model.")
    parser.add_argument("--run_dir", dest="run_dir",
                        type=str, required=False, default=None)
    parser.add_argument("--data_dir", dest="data_dir",
                        type=str, required=False, default=None)
    args = parser.parse_args()
    train(**args.__dict__)

