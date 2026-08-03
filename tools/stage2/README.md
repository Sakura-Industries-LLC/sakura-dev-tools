# Why Stage 2

These dependencies cannot be safely installed by the first `proto` layer, but they can be safely versioned.
We use this file to track their versions.
The `outdated.py` script mirrors those versions into `.env` for dedicated build stages.
