.PHONY: help
help:
	@echo "Make targets for v7vibeforth"
	@echo "make init - Set up dev environment"
	@echo "make update - Update pinned dependencies and run make init"
	@echo "make update-deps - Update pinned dependencies"
	@echo "make c - Rebuild forth and bedit from C source"

.PHONY: init
init:
	uv sync --frozen --all-groups
	uv run prek install

.PHONY: update
update: update-deps init

.PHONY: update-deps
update-deps:
	uv lock --upgrade
	uv run --only-group=lint prek autoupdate

.PHONY: c
c:
	make -C c bedit-test bedit-snap forth-modern bedit-modern

