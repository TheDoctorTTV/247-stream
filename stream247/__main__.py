try:
    from .runtime import main
except ImportError:
    from stream247.runtime import main

if __name__ == "__main__":
    main()
