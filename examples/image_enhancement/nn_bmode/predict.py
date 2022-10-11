# File:       nn_bmode/nn_bmode/predict.py
# Author:     Dongwoon Hyun (dongwoon.hyun@stanford.edu)
# Created on: 2019-09-03
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
import numpy as np

from nn_bmode.utils import load_img_from_mat
from nn_bmode.model import NNBmode
import scipy.io as sio
import argparse


def predict(test_dataset, model_filepath, output_file):
    # Load data
    x = load_img_from_mat(test_dataset)
    # Create model and load trained weights
    model = NNBmode(x[0].shape, model_weights=model_filepath)
    # Run predictions
    p = model.predict(x=x)
    # Transpose data to be read by MATLAB
    p = np.transpose(p, [3, 2, 1, 0])
    # Save output
    sio.savemat(output_file, {"p": p})
    # Clear graph
    tf.keras.backend.clear_session()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Builds NNBmode model.")
    parser.add_argument("--test_dataset", dest="test_dataset",
                        type=str, required=False,
                        default="./data/test_PICMUS.mat")
    parser.add_argument("--model_filepath", dest="model_filepath",
                        type=str, required=False,
                        default="./runs/pretrained/model.h5")
    parser.add_argument("--output_file", dest="output_file",
                        type=str, required=False,
                        default="./runs/pretrained/results_PICMUS.mat")
    args = parser.parse_args()
    predict(**args.__dict__)


