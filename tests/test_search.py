"""
Tests for the search module.
"""

from app.core.search import (
    SuggestionIndex,
    Trie,
    get_suggestion_index,
    reset_suggestion_index,
)


class TestTrie:
    """Tests for the Trie class."""

    def test_insert_and_search(self):
        """Test basic insert and search functionality."""
        trie = Trie()
        trie.insert("hello")
        trie.insert("world")

        assert trie.search("hello")
        assert trie.search("world")
        assert not trie.search("hell")
        assert not trie.search("helloo")

    def test_case_insensitive_search(self):
        """Test that search is case-insensitive."""
        trie = Trie()
        trie.insert("Hello")

        assert trie.search("hello")
        assert trie.search("HELLO")
        assert trie.search("HeLLo")

    def test_starts_with(self):
        """Test prefix checking."""
        trie = Trie()
        trie.insert("hello")
        trie.insert("help")
        trie.insert("world")

        assert trie.starts_with("hel")
        assert trie.starts_with("hello")
        assert trie.starts_with("wor")
        assert not trie.starts_with("xyz")

    def test_find_all_with_prefix(self):
        """Test finding all words with a prefix."""
        trie = Trie()
        words = ["hello", "help", "helper", "helping", "world"]
        for word in words:
            trie.insert(word)

        results = trie.find_all_with_prefix("hel")
        assert len(results) == 4
        assert set(results) == {"hello", "help", "helper", "helping"}

        results = trie.find_all_with_prefix("help")
        assert len(results) == 3
        assert set(results) == {"help", "helper", "helping"}

    def test_find_all_with_prefix_limit(self):
        """Test that prefix search respects limit."""
        trie = Trie()
        for i in range(100):
            trie.insert(f"test{i}")

        results = trie.find_all_with_prefix("test", limit=10)
        assert len(results) == 10

    def test_find_all_with_prefix_and_metadata(self):
        """Test finding words with metadata."""
        trie = Trie()
        trie.insert("hello", {"score": 100})
        trie.insert("help", {"score": 80})

        results = trie.find_all_with_prefix_and_metadata("hel")
        assert len(results) == 2

        hello_result = next(r for r in results if r["word"] == "hello")
        assert hello_result["metadata"]["score"] == 100

    def test_find_containing(self):
        """Test substring search."""
        trie = Trie()
        words = ["hello", "shell", "yellow", "world"]
        for word in words:
            trie.insert(word)

        results = trie.find_containing("ell")
        assert len(results) == 3
        assert set(results) == {"hello", "shell", "yellow"}

    def test_get_all_words(self):
        """Test getting all words from trie."""
        trie = Trie()
        words = ["apple", "banana", "cherry"]
        for word in words:
            trie.insert(word)

        all_words = list(trie.get_all_words())
        assert len(all_words) == 3
        assert set(all_words) == set(words)

    def test_len(self):
        """Test trie length."""
        trie = Trie()
        assert len(trie) == 0

        trie.insert("hello")
        assert len(trie) == 1

        # Duplicate insert shouldn't increase count
        trie.insert("hello")
        assert len(trie) == 1

        trie.insert("world")
        assert len(trie) == 2

    def test_contains(self):
        """Test __contains__ method."""
        trie = Trie()
        trie.insert("hello")

        assert "hello" in trie
        assert "HELLO" in trie  # Case insensitive
        assert "world" not in trie

    def test_empty_string(self):
        """Test handling of empty strings."""
        trie = Trie()
        trie.insert("")  # Should not raise
        assert len(trie) == 0
        assert not trie.search("")


class TestSuggestionIndex:
    """Tests for the SuggestionIndex class."""

    def test_add_suggestion(self):
        """Test adding suggestions."""
        index = SuggestionIndex()
        index.add_suggestion("python tutorial")

        results = index.search_prefix("python")
        assert "python tutorial" in results

    def test_add_suggestion_with_category(self):
        """Test adding suggestions with categories."""
        index = SuggestionIndex()
        index.add_suggestion("python tutorial", category="programming")
        index.add_suggestion("python cookbook", category="books")

        categories = index.get_categories()
        assert "programming" in categories
        assert "books" in categories

    def test_add_suggestions_batch(self):
        """Test batch adding suggestions."""
        index = SuggestionIndex()
        suggestions = ["python 3", "python 2", "python basics"]
        index.add_suggestions_batch(suggestions, category="python")

        results = index.search_prefix("python")
        assert len(results) == 3

    def test_search_prefix(self):
        """Test prefix search."""
        index = SuggestionIndex()
        index.add_suggestion("hello world")
        index.add_suggestion("hello there")
        index.add_suggestion("hi there")

        results = index.search_prefix("hello")
        assert len(results) == 2
        assert "hello world" in results
        assert "hello there" in results

    def test_search_containing(self):
        """Test substring search."""
        index = SuggestionIndex()
        index.add_suggestion("hello world")
        index.add_suggestion("the world is")
        index.add_suggestion("python code")

        results = index.search_containing("world")
        assert len(results) == 2
        assert "hello world" in results
        assert "the world is" in results

    def test_search_in_category(self):
        """Test category-filtered search."""
        index = SuggestionIndex()
        index.add_suggestion("python tutorial", category="programming")
        index.add_suggestion("python cookbook", category="books")
        index.add_suggestion("python basics", category="programming")

        results = index.search_in_category("python", "programming")
        assert len(results) == 2
        assert "python tutorial" in results
        assert "python basics" in results
        assert "python cookbook" not in results

    def test_get_suggestions_in_category(self):
        """Test getting all suggestions in a category."""
        index = SuggestionIndex()
        index.add_suggestion("python 3", category="python")
        index.add_suggestion("python 2", category="python")
        index.add_suggestion("java 11", category="java")

        python_suggestions = index.get_suggestions_in_category("python")
        assert len(python_suggestions) == 2

    def test_clear(self):
        """Test clearing the index."""
        index = SuggestionIndex()
        index.add_suggestion("hello")
        index.add_suggestion("world")

        assert len(index) == 2

        index.clear()
        assert len(index) == 0
        assert len(index.get_categories()) == 0


class TestGlobalSuggestionIndex:
    """Tests for the global suggestion index singleton."""

    def setup_method(self):
        """Reset global index before each test."""
        reset_suggestion_index()

    def teardown_method(self):
        """Clean up after each test."""
        reset_suggestion_index()

    def test_get_suggestion_index(self):
        """Test getting the global index."""
        index1 = get_suggestion_index()
        index2 = get_suggestion_index()

        assert index1 is index2

    def test_reset_suggestion_index(self):
        """Test resetting the global index."""
        index1 = get_suggestion_index()
        index1.add_suggestion("test")

        reset_suggestion_index()

        index2 = get_suggestion_index()
        assert index1 is not index2
        assert len(index2) == 0


class TestTriePerformance:
    """Performance tests for the Trie."""

    def test_large_dataset_insertion(self):
        """Test that large datasets can be inserted efficiently."""
        trie = Trie()

        # Insert 10,000 words
        for i in range(10000):
            trie.insert(f"word{i}")

        assert len(trie) == 10000

    def test_large_dataset_search(self):
        """Test that search is efficient on large datasets."""
        trie = Trie()

        # Insert 10,000 words
        for i in range(10000):
            trie.insert(f"word{i}")

        # Search should be fast
        assert trie.search("word5000")
        assert not trie.search("wordx")

    def test_prefix_search_performance(self):
        """Test prefix search performance."""
        trie = Trie()

        # Insert words with common prefixes
        prefixes = ["python", "java", "javascript", "ruby", "rust"]
        for prefix in prefixes:
            for i in range(1000):
                trie.insert(f"{prefix}_{i}")

        # Prefix search with limit
        results = trie.find_all_with_prefix("python", limit=100)
        assert len(results) == 100
