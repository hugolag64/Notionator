import cProfile, pstats, io
import main  # votre point d’entrée

if __name__ == "__main__":
    pr = cProfile.Profile()
    pr.enable()
    main.App().mainloop()
    pr.disable()

    s = io.StringIO()
    ps = pstats.Stats(pr).sort_stats(pstats.SortKey.CUMULATIVE)
    ps.print_stats(60)  # top 60 lignes
    print(s.getvalue())
