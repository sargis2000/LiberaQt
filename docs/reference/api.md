# Python API

Generated from the docstrings in `src/liberaqt/`, so it cannot drift from the code.

## Entry point

::: liberaqt.LiberaQt
    options:
      members:
        - launch
        - connect
        - timeout

## Application

::: liberaqt.application.Application

## Window

::: liberaqt.window.Window

## Locator

The largest surface, and the one you use most.

::: liberaqt.locator.Locator

## Assertions

::: liberaqt.expect.expect

::: liberaqt.expect.LocatorAssertions

## Selectors

::: liberaqt.selectors
    options:
      members:
        - parse
        - coerce
        - Selector
        - Step
        - Attr

## Errors

::: liberaqt.errors

## Session and object maps

::: liberaqt.session.Session

::: liberaqt.session.ObjectMap

## Waiting

::: liberaqt.waits

## Low-level input

::: liberaqt.mouse.Mouse

::: liberaqt.keyboard.Keyboard

## Agent management

::: liberaqt.agent_registry.AgentBuild

::: liberaqt.agent_registry
    options:
      members:
        - installed
        - resolve
        - inspect_binary
        - abi_key_for
        - cache_dir
        - current_platform_tag

::: liberaqt.agent_install
    options:
      members:
        - install
        - archive_name
        - AgentInstallError

## Launching

::: liberaqt.launcher
    options:
      members:
        - build_environment
        - sibling_plugin_dirs
        - read_endpoints
        - AgentEndpoint
        - LaunchedProcess

## NLview canvases

For EDA tools embedding the NLview schematic engine — Libero SoC, ModelSim, QuestaSim.

::: liberaqt.nlview
    options:
      members:
        - NlviewCanvas
        - NlviewObject
        - parse_result
