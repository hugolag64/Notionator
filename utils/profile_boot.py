# tools/profile_boot.py
import cProfile, pstats, io
import runpy

def main():
    pr = cProfile.Profile()
    pr.enable()
    runpy.run_module("main", run_name="__main__")
    pr.disable()

    s = io.StringIO()
    ps = pstats.Stats(pr, stream=s).sort_stats("cumulative")
    ps.print_stats(50)  # top 50
    print(s.getvalue())

if __name__ == "__main__":
    main()
