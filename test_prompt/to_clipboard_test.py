from pathlib import Path

import pytest
import os
from unittest.mock import patch
from dev_helper.to_clipboard.to_clipboard import generate_ascii_tree, load_gitignore_spec


class TestAsciiTreeFeature:
    
    @pytest.fixture
    def mock_file_structure(self):
        """
        Simulates structured paths for evaluation.
        """
        return [
            "file1.txt",
            "folder/file2.txt",
            "folder/ignored_file.log",
            "large_binary.exe"
        ]

    def test_tree_show_false_returns_empty_string(self):
        """
        Requirement: If ASCII_TREE_SHOW is False, the output must be completely empty.
        """
        result = generate_ascii_tree(".", ["file1.txt"], show_tree=False)
        assert result == ""

    def test_tree_show_true_standard_output(self):
        """
        Requirement: Default visibility builds an ASCII structure using only allowed files.
        """
        valid_files = ["file1.txt", "folder/file2.txt"]
        result = generate_ascii_tree(".", valid_files, show_tree=True, show_ignored=False)
        
        assert "file1.txt" in result
        assert "folder" in result
        assert "file2.txt" in result

    def test_tree_show_ignored_false_skips_filtered_files(self):
        """
        Requirement: When show_ignored is False, non-processable elements 
        must be completely omitted from the tree representation.
        """
        def mock_is_ignored(path):
            return path in ["folder/ignored_file.log", "large_binary.exe"]
            
        files_to_process = ["file1.txt", "folder/file2.txt", "folder/ignored_file.log", "large_binary.exe"]
        
        result = generate_ascii_tree(
            ".", files_to_process, show_tree=True, show_ignored=False, is_ignored_fn=mock_is_ignored
        )
        
        assert "file1.txt" in result
        assert "folder" in result
        assert "file2.txt" in result
        assert "ignored_file.log" not in result
        assert "large_binary.exe" not in result

    def test_tree_show_ignored_true_includes_filtered_files(self):
        """
        Requirement: When show_ignored is True, filtered assets are included 
        in the output index tree layout.
        """
        def mock_is_ignored(path):
            return path == "folder/ignored_file.log"
        
        files_to_process = ["file1.txt", "folder/file2.txt", "folder/ignored_file.log"]
        
        result = generate_ascii_tree(
            ".", files_to_process, show_tree=True, show_ignored=True, is_ignored_fn=mock_is_ignored
        )
        
        assert "file1.txt" in result
        assert "ignored_file.log" in result

    def test_tree_show_ignored_subfolder_constraint(self):
        """
        Requirement (The 'TODO' rule): If a folder block itself matches an ignore rule, 
        render that folder node, but do not traverse or list its sub-elements.
        """
        def mock_is_ignored(path):
            return path.startswith("secret_dir")
        
        files_to_process = [
            "file1.txt", 
            "secret_dir/file2.txt", 
            "secret_dir/sub/file3.txt"
        ]
        
        result = generate_ascii_tree(
            ".", files_to_process, show_tree=True, show_ignored=True, is_ignored_fn=mock_is_ignored
        )
        
        output_lines = [line.strip() for line in result.splitlines()]
        
        assert any("secret_dir" in line for line in output_lines)
        
        assert not any("file2.txt" in line for line in output_lines)
        assert not any("sub" in line for line in output_lines)
        assert not any("file3.txt" in line for line in output_lines)

    def test_ancestor_collapse_ignored_subfolder_excluded(self):
        files = ["src/app/main.py", "node_modules/package/index.js"]
        def is_ignored(path):
            return path.startswith("node_modules")
        result = generate_ascii_tree(".", files, show_tree=True, show_ignored=False, is_ignored_fn=is_ignored)
        assert "src" in result
        assert "app" not in result or "node_modules" not in result
        assert "node_modules" not in result
        assert "index.js" not in result

    def test_ancestor_collapse_via_ancestor_not_node(self):
        files = ["src/app/main.py"]
        def is_ignored(path):
            return path == "src/app"
        result = generate_ascii_tree(".", files, show_tree=True, show_ignored=True, is_ignored_fn=is_ignored)
        assert "src" in result
        assert "app" in result
        assert "main.py" not in result

    def test_directory_pattern_git_folder_show_ignored_false(self):
        """
        When a directory pattern like '.git/' is ignored and show_ignored=False,
        the directory and ALL its contents must be completely absent from the tree.
        """
        def mock_is_ignored(path):
            if path == ".git" or path.startswith(".git/"):
                return True
            return False
        
        files = [".git/ignored.txt", "1/a.txt", "2/b.txt", "c.txt"]
        
        result = generate_ascii_tree(
            ".", files, show_tree=True, show_ignored=False, is_ignored_fn=mock_is_ignored
        )
        
        assert ".git" not in result
        assert "ignored.txt" not in result
        assert "a.txt" in result
        assert "b.txt" in result
        assert "c.txt" in result

    def test_directory_pattern_git_folder_show_ignored_true(self):
        """
        When a directory itself is ignored (e.g. '.git') and show_ignored=True,
        the tree renders only the stubbed directory node with '[IGNORED]' suffix,
        and no contents are listed.
        """
        def mock_is_ignored(path):
            if path == ".git" or path == ".git/" or path.startswith(".git/"):
                return True
            return False
        
        result = generate_ascii_tree(
            ".git", [".git/ignored.txt", "1/a.txt"], show_tree=True, show_ignored=True, is_ignored_fn=mock_is_ignored
        )
        
        assert ".git" in result
        assert "ignored.txt" not in result

    def test_trailing_slash_directory_pattern_matching(self):
        """
        Patterns like '.git/' (with trailing slash) must match the directory name
        itself. Two scenarios:
        1. The copied folder IS the ignored directory -> stubbed as '[IGNORED]'.
        2. An ignored subdirectory inside the copied folder -> shown as '[IGNORED]'
           with no contents when show_ignored=True.
        """
        def mock_is_ignored(path):
            if path == ".git" or path == ".git/" or path.startswith(".git/"):
                return True
            if path == "node_modules" or path == "node_modules/" or path.startswith("node_modules/"):
                return True
            return False
        
        result_git = generate_ascii_tree(
            ".", [".git/config"], show_tree=True, show_ignored=True, is_ignored_fn=mock_is_ignored
        )
        assert ".git" in result_git
        assert "config" not in result_git
        
        result_node = generate_ascii_tree(
            ".", ["node_modules/pkg/index.js"], show_tree=True, show_ignored=True, is_ignored_fn=mock_is_ignored
        )
        assert "node_modules" in result_node
        assert "index.js" not in result_node

    def test_gitignore_present_without_git_folder(self):
        """
        When .gitignore exists but .git folder is absent,
        generate_ascii_tree should still work correctly when called
        with explicit paths and a base_path that does not exist on disk.
        """
        spec = load_gitignore_spec(os.path.dirname(os.path.abspath(__file__)))
        assert spec is not None

        def mock_is_ignored(path):
            return spec.match_file(path)

        result = generate_ascii_tree(
            "nonexistent_dir",
            ["src/main.py", "src/utils.py"],
            show_tree=True,
            show_ignored=False,
            is_ignored_fn=mock_is_ignored,
        )

        assert "src" in result
        assert "main.py" in result
        assert "utils.py" in result

    def test_gitignore_with_no_git_folder_show_ignored_true(self):
        """
        When .gitignore exists but .git folder is absent and show_ignored=True,
        the tree should render normally from explicit paths even with a
        non-existent base_path.
        """
        spec = load_gitignore_spec(os.path.dirname(os.path.abspath(__file__)))

        def mock_is_ignored(path):
            if spec:
                if spec.match_file(path):
                    return True
                if not path.endswith("/") and spec.match_file(path + "/"):
                    return True
            return False

        result = generate_ascii_tree(
            "nonexistent_dir",
            [".git/config", "app/app.py", "README.md"],
            show_tree=True,
            show_ignored=True,
            is_ignored_fn=mock_is_ignored,
        )

        assert "app" in result
        assert "app.py" in result
        assert "README.md" in result

    def test_ignored_empty_folder_groups_with_folders_not_files(self):
        files = [
            "a_folder/inner.txt",
            "m_file.txt",
            "z_folder/ignored.log",
        ]
        def mock_is_ignored(path):
            return path == "z_folder"

        result = generate_ascii_tree(
            ".", files, show_tree=True, show_ignored=True, is_ignored_fn=mock_is_ignored
        )
        lines = [line.strip() for line in result.splitlines()]
        root_connectors = [
            line for line in lines
            if line.startswith(("├── ", "└── "))
        ]
        name_entries = [line[4:] for line in root_connectors]
        assert name_entries == ["a_folder", "z_folder", "m_file.txt"]

    def test_tree_sort_folders_before_files(self):
        files = [
            "z_file.txt",
            "a_folder/inside.txt",
            "m_folder/nested.txt",
            "b_file.txt",
        ]
        result = generate_ascii_tree(".", files, show_tree=True, show_ignored=False)
        lines = [line.strip() for line in result.splitlines()]
        root_connectors = [
            line for line in lines
            if line.startswith(("├── ", "└── "))
        ]

        name_entries = [line[4:] for line in root_connectors]
        assert name_entries == ["a_folder", "m_folder", "b_file.txt", "z_file.txt"]

    def test_no_folders_no_tree(self):
        """
        Requirement: When there are no folders (all files at root level),
        no ASCII tree should be produced regardless of ASCII_TREE_SHOW setting.
        """
        files = ["file1.txt", "file2.txt"]
        result = generate_ascii_tree(".", files, show_tree=True, show_ignored=False)
        
        assert result == ""

    def test_single_root_file_no_tree(self):
        """
        Requirement: Single file at root level produces no tree.
        The tree format is unnecessary when all files share the same parent directory.
        """
        files = ["single_file.txt"]
        result = generate_ascii_tree(".", files, show_tree=True, show_ignored=False)
        
        assert result == ""

    def test_one_root_file_one_nested_file_produces_tree(self):
        """
        Requirement: One file at root level and one file in a folder
        should produce an ASCII tree since there's folder structure.
        """
        files = ["root_file.txt", "folder/nested_file.txt"]
        result = generate_ascii_tree(".", files, show_tree=True, show_ignored=False)
        
        assert "root_file.txt" in result
        assert "folder" in result
        assert "nested_file.txt" in result
