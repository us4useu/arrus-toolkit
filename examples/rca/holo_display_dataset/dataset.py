import os.path
import urllib.request
import pickle


def load_rca_cyst_dataset():
    filename = "rca_cyst.pickle"
    if not os.path.exists(filename):
        url = "https://www.dropbox.com/scl/fi/6vpt9uxne7pilhbhbtw24/vermon_rca_dataset.pickle?rlkey=pik6wku00su0b9um3p5xdb6hf&dl=1"
        print("Downloading dataset...")
        urllib.request.urlretrieve(url, filename)
        print("... dataset downloaded.")
    data = pickle.load(open(filename, "rb"))
    rf = data["rf"]
    metadata = data["metadata"][0]
    return rf, metadata

