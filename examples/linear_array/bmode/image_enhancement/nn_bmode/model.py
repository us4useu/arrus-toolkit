# File:       nn_bmode/nn_bmode/model.py
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
from tensorflow.keras import layers
import nn_bmode.losses as losses


class NNBmode:

    def __init__(self, input_shape, n_layers=8, n_filters=32, kernel_size=3,
                 optimizer=None, loss=None, metrics=None, model_weights=None):
        if optimizer is None:
            optimizer = tf.keras.optimizers.Adam(1e-3)
        if loss is None:
            loss = losses.get_l1loss_wopt_log
        if metrics is None:
            metrics = [
                losses.get_l1loss_wopt_log,
                losses.get_l2loss_wopt_log,
                losses.get_msssim_wopt_log,
            ]
        self.model = self._create_model(
            input_shape, n_layers, n_filters, kernel_size,
            optimizer, loss, metrics, model_weights)

    def _create_model(self, input_shape, n_layers, n_filters, kernel_size,
                      optimizer, loss, metrics, model_weights):
        inputs = tf.keras.layers.Input(shape=input_shape, dtype=tf.float32,
                                       name="input")
        x = inputs
        # Convolution, activation, batch normalization
        for _ in range(n_layers):
            x = layers.Conv2D(
                filters=n_filters,
                kernel_size=kernel_size,
                padding="same",
                data_format="channels_first",
                activation="relu",
            )(x)
            # x = layers.BatchNormalization(axis=1)(x)

        # Concatenate input to end of convolution blocks
        x = layers.Concatenate(axis=1)([x, inputs])

        # Convert filters into a single filter
        x = layers.Conv2D(filters=1, kernel_size=1,
                          data_format="channels_first")(x)

        # Square to enforce non-negative
        output = layers.Lambda(lambda z: z * z + 1e-32)(x)
        model = tf.keras.Model(inputs=inputs, outputs=output)
        # TODO is the compile needed when the model is used only for inference?
        model.compile(optimizer, loss=loss, metrics=metrics)
        if model_weights is not None:
            model.load_weights(model_weights)
        return model

    def predict(self, x):
        return self.model.predict_on_batch(x)

    def fit(self, **kwargs):
        return self.model.fit(**kwargs)

    def save_weights(self, filepath, save_format):
        self.model.save_weights(filepath, save_format=save_format)
