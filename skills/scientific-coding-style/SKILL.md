---
name: scientific-coding-style
description: describes how to write code to be read and validated by human scientists.
---

The code you write will be read and checked by scientists, not software engineers. 

Keep it simple and short. The user will read all of the code so brevity is essential.

Avoid defensive programming, catching edge cases (try/catch) and type checking unless absolutely necessary. It is better for code to occasionally crash than to be complex. It is better to crash than to silently produce incorrect results.  

Avoid classes unless they increase clarity. Avoid clever code that is hard to read.  

**Do** use vectorization wherever possible. For speed and also brevity; scientists will find this easier to read than loops. 

 Use comments to explain the scientific reasoning behind your code.  Use descriptive variable names. Each function should have a docstring explaining what it does, what its inputs and outputs are (including array sizes), and any assumptions it makes.