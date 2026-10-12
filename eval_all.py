import runpy, pathlib
target = pathlib.Path(__file__).parent / "tools" / "eval" / "eval_all.py"
runpy.run_path(str(target), run_name="__main__")
