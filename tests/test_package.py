def test_package_imports_and_has_version():
    import riskdyn
    assert isinstance(riskdyn.__version__, str)
    assert riskdyn.__version__.count(".") >= 1
