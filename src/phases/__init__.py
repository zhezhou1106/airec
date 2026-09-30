"""Pipeline phases, one file each. Every phase reads its input from the index and
saves its output there, so a stopped run resumes where it left off:

    knowledge   collect → embed → resurface → triage → score → select → read
    news        collect → embed → group → write news section
    digest      write the article, render, save
"""
