Connecting new probe
--------------------

In this section, we outline the steps that should be taken when you want to connect a new head to our system.

If your head is not on the list of heads supported by us (see the list here), you will likely need to determine the probe parameters and pin-mapping between the probe and the ultrasound system. For that, please refer to Section 1, "Determining pin-mapping."

If your head model is officially supported by our software, you can use this prototxt file, just remember to set the appropriate name of the adapter and probe model.


Determining pin-mapping
~~~~~~~~~~~~~~~~~~~~~~~

1. Download this `custom_mapping_dlp408r.prototxt` file, set the appropriate number of elements and pitch.
2. Download the script that allows you to display RF data in real time: Python, Matlab, and set the appropriate .prototxt file in it (please check the `arrus.Session` (Python) or the `Us4R` (MATLAB) constructor).
3. Run the script and check if you can see a correct echo from a point target (as shown below).
4. If not, it will be necessary to adjust the pin mapping between the system channels and the head elements. To do this, try modifying `probe_to_adapter_connection` until you obtain the correct result. You can read more about the `probe_to_adapter_connection` field here.
5. Finally, make sure you see a correct RF signal on all channels. If you do not receive a correct signal from a strong reflector at a particular probe element, please mask it by adding it to this list.

