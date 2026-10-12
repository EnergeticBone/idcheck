import runpy, pathlib
target = pathlib.Path(__file__).parent / "tools" / "utils" / "make_injected.py"
runpy.run_path(str(target), run_name="__main__")
