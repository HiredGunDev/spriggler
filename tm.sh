#!/bin/bash
echo "=== Project Structure ==="
tree
echo
for file in $(find . -name "*.py" -type f); do
    echo "=== $file ==="
    cat "$file"
    echo
done

