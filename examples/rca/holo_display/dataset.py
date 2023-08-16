import urllib.request
import pickle


def load_rca_cyst_dataset():
    url = "https://www.dropbox.com/scl/fi/6vpt9uxne7pilhbhbtw24/vermon_rca_dataset.pickle?rlkey=pik6wku00su0b9um3p5xdb6hf&dl=1"
    print("Downloading dataset...")
    urllib.request.urlretrieve(url, "rca_cyst.pickle")
    print("... dataset downloaded.")
    data = pickle.load(open("rca_cyst.pickle", "rb"))
    rf = data["rf"]
    metadata = data["metadata"][0]
    return rf, metadata

