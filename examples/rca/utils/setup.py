import setuptools

setuptools.setup(
    name="arrus_rca_utils",
    version="0.1.0",
    author="us4us Ltd.",
    author_email="support@us4us.eu",
    description="Utility functions to work with ARRUS and RCA probe",
    long_description="Utility functions to work with ARRUS and RCA probe",
    long_description_content_type="text/markdown",
    url="https://us4us.eu",
    packages=setuptools.find_packages(exclude=[]),
    include_package_data=True,
    package_data={
        "arrus_rca_utils": ["*.cu"]
    },
    classifiers=[
        "Development Status :: 1 - Planning",

        "Intended Audience :: Developers",
        "Intended Audience :: Science/Research",

        "Programming Language :: Python :: 3",
        "Operating System :: OS Independent",
        "Topic :: Software Development :: Embedded Systems",
        "Topic :: Scientific/Engineering :: Bio-Informatics",
        "Topic :: Scientific/Engineering :: Medical Science Apps."
    ],
    python_requires='>=3.8'
)
