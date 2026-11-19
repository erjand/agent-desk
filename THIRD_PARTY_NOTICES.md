# Third-party notices

agent-desk is under the MIT license in `LICENSE`. The vendored code below keeps its own license.

## `web/vendor/preact-htm.js`

The file is htm 3.1.1's `preact/standalone.module.js` from npm, a single build of htm and Preact 10 with its hooks.

### htm 3.1.1

- Source: <https://github.com/developit/htm>
- License: Apache License 2.0, copyright 2018 Google Inc.
- Full text: [`web/vendor/LICENSE-htm`](web/vendor/LICENSE-htm)

### Preact 10

- Source: <https://github.com/preactjs/preact>
- License: MIT, copyright 2015-present Jason Miller

```text
The MIT License (MIT)

Copyright (c) 2015-present Jason Miller

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## `web/vendor/marked.esm.js`

The file is marked 18.2.0's `lib/marked.esm.js` from npm, unchanged. `web/markdown.ts` parses Markdown with it on the
server and cleans its output with its own sanitizer.

- Source: <https://github.com/markedjs/marked>
- License: MIT, copyright 2018+ MarkedJS and 2011-2018 Christopher Jeffrey, and the BSD-style license of John
  Gruber's original Markdown, copyright 2004
- Full text: [`web/vendor/LICENSE-marked`](web/vendor/LICENSE-marked)
