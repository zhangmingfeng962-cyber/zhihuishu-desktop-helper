"""Executable entry point; deliberately does not import the legacy browser engine."""
if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument('--selftest-course',action='store_true')
    parser.add_argument('--report-dir',default='course-test-results')
    parser.add_argument('--resume-test',action='store_true')
    args=parser.parse_args()
    if args.selftest_course:
        from desktop_app.course_selftest import main
        raise SystemExit(main(args.report_dir,args.resume_test))
    from desktop_app.gui import main
    main()
