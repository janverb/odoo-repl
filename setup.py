import setuptools

setuptools.setup(
    name="odoo_repl",
    version="0.0.1",
    author="Jan Verbeek",
    author_email="jverbeek@therp.nl",
    description="Enhanced interactive Odoo buildout prompt",
    # url=
    packages=setuptools.find_packages(),
    entry_points={
        'console_scripts': [
            'odoo-repl = odoo_repl.run:main',
        ],
    },
    # license=
    # classifiers=
    # keywords=
    # long_description=
    # long_description_content_type=
    # project_urls=
)
