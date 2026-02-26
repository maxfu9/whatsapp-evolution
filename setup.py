from setuptools import setup, find_packages

with open("requirements.txt") as f:
    install_requires = f.read().strip().split("\n")

# get version from __version__ variable in whatsapp_evolution/__init__.py
from whatsapp_evolution import __version__ as version

setup(
    name="whatsapp-evolution",
    version=version,
    description="WhatsApp integration for frappe",
    author="Europlast",
    author_email="hello@europlast.pk",
    packages=find_packages(),
    zip_safe=False,
    include_package_data=True,
    install_requires=install_requires
)
