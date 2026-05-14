from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class NavItem:
    key: str
    title: str
    page_index: int = -1
    action_key: str = ""
    badge: str = ""
    tooltip: str = ""
    icon_key: str = ""
    visible: bool = True
    enabled: bool = True


@dataclass(slots=True)
class NavSection:
    key: str
    title: str
    items: list[NavItem] = field(default_factory=list)
    compact_when_collapsed: bool = True


_PAGE_CONFIGS: list[dict[str, str | int]] = [
    {
        "key": "dashboard",
        "category": "Workspace",
        "title": "Dashboard",
        "subtitle": "Today's focus overview and upcoming work.",
        "page_index": 0,
        "section": "workspace",
        "tooltip": "Today overview",
        "icon_key": "mdi6.view-dashboard-outline",
    },
    {
        "key": "calendar",
        "category": "Workspace",
        "title": "Calendar",
        "subtitle": "Flexible day range, next-task mode, and rescheduling.",
        "page_index": 1,
        "section": "workspace",
        "tooltip": "Calendar and schedule",
        "icon_key": "mdi6.calendar-blank-outline",
    },
    {
        "key": "planner",
        "category": "Workspace",
        "title": "Planner",
        "subtitle": "Solver-assisted planning and daily structure.",
        "page_index": 2,
        "section": "workspace",
        "tooltip": "Planning surface",
        "icon_key": "mdi6.timeline",
    },
    {
        "key": "tasks",
        "category": "Workspace",
        "title": "Tasks",
        "subtitle": "Create, edit, schedule, and clean up work.",
        "page_index": 3,
        "section": "workspace",
        "tooltip": "Tasks and groups",
        "icon_key": "mdi6.check-circle-outline",
    },
    {
        "key": "coach",
        "category": "Workspace",
        "title": "Coach",
        "subtitle": "Weekly review, decomposition, and natural-language task drafting.",
        "page_index": 4,
        "section": "workspace",
        "tooltip": "Weekly review and coaching",
        "icon_key": "mdi6.account-tie-outline",
    },
    {
        "key": "account",
        "category": "Workspace",
        "title": "Account",
        "subtitle": "Manage your account and preferences.",
        "page_index": 5,
        "section": "workspace",
        "tooltip": "Account management",
        "icon_key": "mdi6.account-circle-outline",
    },
    {
        "key": "notifications",
        "category": "Tools",
        "title": "Notifications",
        "subtitle": "Warnings, interventions, and reviewable events.",
        "page_index": 6,
        "section": "tools",
        "tooltip": "Notifications and interventions",
        "icon_key": "mdi6.bell-outline",
    },
    {
        "key": "plugins",
        "category": "Tools",
        "title": "Plugins",
        "subtitle": "Install, enable, disable, and inspect extension hooks.",
        "page_index": 7,
        "section": "tools",
        "tooltip": "Plugins manager",
        "icon_key": "mdi6.puzzle-outline",
    },
    {
        "key": "settings",
        "category": "Tools",
        "title": "Settings",
        "subtitle": "Account, appearance, and integrations.",
        "page_index": 8,
        "section": "tools",
        "tooltip": "Application settings",
        "icon_key": "mdi6.cog-outline",
    },
]

PAGE_ORDER: list[str] = [str(config["key"]) for config in _PAGE_CONFIGS]
NAV_PAGE_ORDER: list[str] = PAGE_ORDER[:]

HEADER_MAP = {
    str(config["key"]): {
        "category": str(config["category"]),
        "title": str(config["title"]),
        "subtitle": str(config["subtitle"]),
    }
    for config in _PAGE_CONFIGS
}


def build_nav_sections() -> list[NavSection]:
    sections: dict[str, NavSection] = {
        "workspace": NavSection(key="workspace", title="Workspace"),
        "tools": NavSection(key="tools", title="Tools"),
    }

    for config in _PAGE_CONFIGS:
        section_key = str(config["section"])
        section = sections.get(section_key)
        if section is None:
            section = NavSection(key=section_key, title=str(section_key).title())
            sections[section_key] = section
        section.items.append(
            NavItem(
                key=str(config["key"]),
                title=str(config["title"]),
                page_index=int(config["page_index"]),
                tooltip=str(config["tooltip"]),
                icon_key=str(config["icon_key"]),
            )
        )

    return [sections["workspace"], sections["tools"]]


__all__ = [
    "NavItem",
    "NavSection",
    "PAGE_ORDER",
    "NAV_PAGE_ORDER",
    "HEADER_MAP",
    "build_nav_sections",
]
