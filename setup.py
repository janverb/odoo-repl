# -*- coding: utf-8 -*-

import os
import setuptools

with open(os.path.join(os.path.dirname(__file__), "README.md")) as f:
    long_description = f.read()

setuptools.setup(
    name="odoo-repl",
    version="0.0.2",
    author="Jan Verbeek",
    author_email="jverbeek@therp.nl",
    description="Enhanced interactive Odoo shell",
    url="https://github.com/janverb/odoo-repl",
    packages=setuptools.find_packages(),
    package_data={"odoo_repl": ["py.typed"]},
    # It would be nice to make the script work with things besides buildout
    entry_points={"console_scripts": ["odoo-repl = odoo_repl.buildout:main"]},
    install_requires=["Pygments"],
    license="LGPLv3+",
    classifiers=[
        "Framework :: Odoo",
        "Framework :: Buildout",
        "Development Status :: 3 - Alpha",
        "License :: OSI Approved :: GNU Lesser General Public License v3 or "
        "later (LGPLv3+)",
        "Intended Audience :: Developers",
        "Environment :: Console",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
        "Topic :: Software Development",
    ],
    keywords="Odoo Interactive Shell REPL",
    long_description=long_description,
    long_description_content_type="text/markdown",
    options={"bdist_wheel": {"universal": "1"}},
)
