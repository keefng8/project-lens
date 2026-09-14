# Project Lens

A heads-up display for everything you are building. Finds your projects, then shows what each is written in, what state git is in, what it depends on, what you left a TODO about and what you touched this week. Opens straight onto a project if Mavis names one.

A feature for [Mavis AI](https://www.mavis-ai.com) — a desktop voice assistant.

```
You: "open project lens"
```

Mavis opens it and stands its own panels down so they are not in your way. Say *"show the interface"* to bring them back.

## Install

From the Mavis Appstore — find **Project Lens** and click Install.

Or install it directly:

```python
from utils.feature_install import install_from_github
install_from_github("https://github.com/keefng8/project-lens")
```

## What you can say

- *"open project lens"*
- *"show me my projects"*
- *"tell me about the mavis website project"*
- *"what state is that project in"*

These are not matched word for word. Mavis gives them to its language model as examples of intent, so close variations work too.

## How it works

A heads-up display for everything you are building: languages, git state, dependencies, TODOs and what you touched this week, across every project it can find on your drives. It is the first feature to use Mavis's new parameter passing - it declares a 'project' parameter in its manifest, so 'tell me about the Mavis website' opens straight onto that project instead of just launching the window. Scanning runs on a worker thread and results come back through a queue, because Tk is not thread-safe and touching a widget from a worker produces crashes you cannot reproduce.

## Requirements

None. A single HTML file — it runs in your browser, offline, and nothing leaves your machine.

## Building your own

See [Building features for Mavis](https://github.com/keefng8/mavis-feature-docs) — a feature is just a GitHub repository with a `mavis.json`.

## License

MIT — see [LICENSE](LICENSE).
