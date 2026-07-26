from lighthouse.code_foundry import changed_paths_from_unified_patch


def test_patch_path_normalisation_tracks_additions_deletions_and_renames():
    patch = """diff --git a/src/old.py b/src/new.py
--- a/src/old.py
+++ b/src/new.py
@@ -1 +1 @@
-old
+new
diff --git a/src/deleted.py b/src/deleted.py
--- a/src/deleted.py
+++ /dev/null
@@ -1 +0,0 @@
-deleted
diff --git a/src/created.py b/src/created.py
--- /dev/null
+++ b/src/created.py
@@ -0,0 +1 @@
+created
"""

    assert changed_paths_from_unified_patch(patch) == (
        "src/old.py",
        "src/new.py",
        "src/deleted.py",
        "src/created.py",
    )
