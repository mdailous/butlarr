from loguru import logger
from typing import Optional, List, Any, Literal
from dataclasses import dataclass, replace
from requests.models import Response

from . import ArrService, ArrVariant, Action, ServiceContent, find_first
from .ext import ExtArrService
from ..tg_handler import command, callback, handler
from ..tg_handler.message import (
    Response,
    repaint,
    clear,
)
from ..tg_handler.auth import authorized, AuthLevels, get_auth_level_from_message
from ..tg_handler.session_state import (
    sessionState,
    default_session_state_key_fn,
)
from ..tg_handler.keyboard import Button, keyboard


@dataclass(frozen=True)
class SeasonState:
    available: List[int]
    selected: List[int]


@dataclass(frozen=True)
class State:
    items: List[Any]
    index: int
    quality_profile: str
    language_profile: str
    tags: List[str]
    root_folder: str
    use_season_folder: bool
    seasons: SeasonState
    menu: Optional[
        Literal["path"]
        | Literal["tags"]
        | Literal["quality"]
        | Literal["language"]
        | Literal["useseasonfolder"]
        | Literal["add"]
    ]


@handler
class Sonarr(ExtArrService, ArrService):
    def __init__(
        self,
        commands: List[str],
        api_host: str,
        api_key: str,
    ):
        self.commands = commands
        self.api_key = api_key

        self.api_version = self.detect_api(api_host)
        self.service_content = ServiceContent.SERIES
        self.arr_variant = ArrVariant.SONARR
        self.root_folders = self.get_root_folders()
        self.quality_profiles = self.get_quality_profiles()
        self.language_profiles = self.get_language_profiles()

    def _get_season_state(self, item):
        available_seasons = [e.get("seasonNumber") for e in item.get("seasons")]
        monitored_seasons = []

        return SeasonState(
            available_seasons,
            monitored_seasons,
        )

    @keyboard
    def keyboard(self, state: State, allow_edit=None):
        item = state.items[state.index]
        in_library = "id" in item and item["id"]

        rows_menu = []
        if state.menu == "add":
            if in_library:
                row_navigation = [Button("=== Editing Series ===", "noop")]
            else:
                row_navigation = [Button("=== Adding Series ===", "noop")]
            rows_menu = [
                [
                    Button(
                        f"Change Quality   ({state.quality_profile.get('name', '-')})",
                        self.get_clbk("quality", state.index),
                    ),
                ],
                [
                    Button(
                        f"Change Path   ({state.root_folder.get('path', '-')})",
                        self.get_clbk("path", state.index),
                    )
                ],
                [
                    Button(
                        f"Change Use Season Folders   ({'✅' if state.use_season_folder else '❌'})",
                        self.get_clbk("useseasonfolder", (not state.use_season_folder)),
                    )
                ],
                [
                    Button(
                        f"Change Language   ({state.language_profile.get('name', '-')})",
                        self.get_clbk("language", state.index),
                    )
                ],
                #      [
                #          Button(
                #              f"Change Tags   (Total: {len(state.tags)})",
                #              self.get_clbk("tags", state.index),
                #          ),
                #      ],
            ]

        elif state.menu == "seasons":
            row_navigation = [Button("=== Search for Seasons ===")]
            rows_menu = [
                [
                    Button(
                        f"{'✔' if id in state.seasons.selected else '🔍'} Season {id}",
                        self.get_clbk(
                            (
                                "noop"
                                if id in state.seasons.selected
                                else "searchseason"
                            ),
                            id,
                        ),
                    )
                ]
                for id in state.seasons.available
            ]
        elif state.menu == "tags":
            row_navigation = [Button("=== Selecting Tags ===")]
            tags = self.get_tags() or []
            rows_menu = [
                (
                    [
                        Button(
                            (
                                f"Tag {tag.get('label', '-')}"
                                if tag not in state.tags
                                else f"Remove {tag.get('label', '-')}"
                            ),
                            self.get_clbk("addtag", tag.get("id")),
                        )
                    ]
                    for tag in tags
                ),
                [
                    Button(
                        "Done",
                        self.get_clbk("addmenu"),
                    )
                ],
            ]
        elif state.menu == "path":
            row_navigation = [Button("=== Selecting Root Folder ===")]
            rows_menu = [
                [
                    Button(
                        p.get("path", "-"),
                        self.get_clbk("selectpath", p.get("id")),
                    )
                ]
                for p in self.root_folders
            ]
        elif state.menu == "quality":
            row_navigation = [Button("=== Selecting Quality Profile ===")]
            rows_menu = [
                [
                    Button(
                        p.get("name", "-"),
                        self.get_clbk("selectquality", p.get("id")),
                    )
                ]
                for p in self.quality_profiles
            ]
        elif state.menu == "language":
            row_navigation = [Button("=== Selecting Language Profile ===")]
            rows_menu = [
                [
                    Button(
                        p.get("name", "-"),
                        self.get_clbk("selectlanguage", p.get("id")),
                    )
                ]
                for p in self.language_profiles
            ]
        elif state.menu == "useseasonfolder":
            row_navigation = [Button("=== Change Season Folders ===")]
            rows_menu = [
                [
                    Button(
                        f"{'❌ Single Series Folder' if state.use_season_folder else '✅ Use Season Folders'}",
                        self.get_clbk("useseasonfolder", (not state.use_season_folder)),
                    )
                ]
            ]
        else:
            if in_library:
                monitored = item.get("monitored", True)
                missing = not item.get("hasFile", False)
                rows_menu = [
                    # Allow manual season search for already added entries
                    [
                        Button(
                            f"🔍 Search for Seasons",
                            self.get_clbk("seasons", state.index),
                        ),
                    ],
                    [
                        Button("📺 Monitored" if monitored else "Unmonitored"),
                        Button("💾 Missing" if missing else "Downloaded"),
                    ],
                ]
            row_navigation = [
                (
                    Button("⬅ Prev", self.get_clbk("goto", state.index - 1))
                    if state.index > 0
                    else Button()
                ),
                (
                    # TODO pjordan: Find replacement
                    Button(
                        "TMDB",
                        url=f"https://www.themoviedb.org/movie/{item['tmdbId']}",
                    )
                    if item.get("tmdbId", None)
                    else None
                ),
                (
                    Button("IMDB", url=f"https://imdb.com/title/{item['imdbId']}")
                    if item.get("imdbId", None)
                    else None
                ),
                (
                    Button("Next ➡", self.get_clbk("goto", state.index + 1))
                    if state.index < len(state.items) - 1
                    else Button()
                ),
            ]

        rows_action = []
        if in_library:
            if allow_edit:
                if state.menu != "add":
                    rows_action.append(
                        [
                            Button(f"🗑 Remove", self.get_clbk("remove")),
                            Button(f"✏️ Edit", self.get_clbk("addmenu")),
                        ]
                    )
                else:
                    rows_action.append(
                        [
                            Button(f"🗑 Remove", self.get_clbk("remove")),
                            Button(f"✅ Submit", self.get_clbk("add", "no-search")),
                        ]
                    )
                    rows_action.append(
                        [
                            Button(
                                f"✅ + 🔍 Submit & Search",
                                self.get_clbk("add", "search"),
                            ),
                        ]
                    )
        else:
            if not state.menu:
                rows_action.append([Button(f"➕ Add", self.get_clbk("addmenu"))])
            elif state.menu == "add":
                rows_action.append(
                    [
                        Button(
                            f"📚 Add (No Monitor)", self.get_clbk("add", "no-monitor")
                        ),
                    ]
                )
                rows_action.append(
                    [
                        Button(f"📺 Monitor All", self.get_clbk("add", "no-search")),
                        Button(f"🔍 Monitor & Search", self.get_clbk("add", "search")),
                    ]
                )

        if state.menu:
            rows_action.append(
                [
                    Button(
                        "🔙 Back",
                        self.get_clbk(
                            "goto"
                            if state.menu and state.menu == "seasons"
                            else (
                                "addmenu"
                                if state.menu and state.menu != "add"
                                else "goto"
                            )
                        ),
                    )
                ]
            )
        else:
            rows_action.append([Button("❌ Cancel", self.get_clbk("cancel"))])

        return [row_navigation, *rows_menu, *rows_action]

    def create_message(self, state: State, full_redraw=False, allow_edit=False):
        if not state.items:
            return Response(
                caption="No series found",
                state=state,
            )

        item = state.items[state.index]

        keyboard_markup = self.keyboard(state, allow_edit=allow_edit)

        reply_message = f"{item['title']} "
        if item["year"] and str(item["year"]) not in item["title"]:
            reply_message += f"({item['year']}) "

        if item["runtime"]:
            reply_message += f"{item['runtime']}min "

        reply_message += f"- {item['status'].title()}\n\n{item.get('overview', '')}"
        reply_message = reply_message[0:1024]

        cover_url = item.get("remotePoster")
        if not cover_url and len(item.get("images")):
            cover_url = item.get("images")[0]["remoteUrl"]

        return Response(
            photo=cover_url if full_redraw else None,
            caption=reply_message,
            reply_markup=keyboard_markup,
            state=state,
        )
    
    def _get_initial_state(self, items):
        return State(
            items=items,
            index=0,
            root_folder=(
                find_first(
                    self.root_folders,
                    lambda x: items[0].get("folderName").startswith(x.get("path")),
                )
                if items
                else None
            ),
            quality_profile=(
                find_first(
                    self.quality_profiles,
                    lambda x: items[0].get("qualityProfileId") == x.get("id"),
                )
                if items
                else None
            ),
            language_profile=(
                find_first(
                    self.language_profiles,
                    lambda x: items[0].get("languageProfileId") == x.get("id"),
                )
                if items
                else None
            ),
            tags=items[0].get("tags", []) if items else None,
            menu=None,
            seasons=self._get_season_state(items[0]),
            use_season_folder=self.get_use_season_folder(items[0]),
        )

    def get_use_season_folder(self, id):
        return self.request(f"series/{id}", fallback={})

    @repaint
    @command(
        default=True,
        default_pattern="<title>",
        default_description="Search for a series",
        cmds=[("search", "<title>", "Search for a series")],
    )
    @sessionState(init=True)
    @authorized(min_auth_level=AuthLevels.USER.value)
    async def cmd_default(self, update, context, args):
        if len(args) > 1 and args[0] == "search":
            args = args[1:]
        title = " ".join(args)

        items = self.lookup(title)

        state = self._get_initial_state(items)

        self.session_db.add_session_entry(
            default_session_state_key_fn(self, update), state
        )

        auth_level = get_auth_level_from_message(self.db, update)
        allow_edit = auth_level >= AuthLevels.MOD.value
        return self.create_message(state, full_redraw=True, allow_edit=allow_edit)

    @command(cmds=[("help", "", "Shows only the sonarr help page")])
    async def cmd_help(self, update, context, args):
        return await ExtArrService.cmd_help(self, update, context, args)

    @repaint
    @command(cmds=[("queue", "", "Shows the sonarr download queue")])
    @authorized(min_auth_level=AuthLevels.USER.value)
    async def cmd_queue(self, update, context, args):
        return await ExtArrService.cmd_queue(self, update, context, args)

    @repaint
    @callback(cmds=["queue"])
    @authorized(min_auth_level=AuthLevels.USER.value)
    async def clbk_queue(self, update, context, args):
        return await ExtArrService.clbk_queue(self, update, context, args)

    @repaint
    @command(cmds=[("list", "", "List all series in the library")])
    @authorized(min_auth_level=AuthLevels.USER.value)
    async def cmd_list(self, update, context, args):
        items = self.list_()

        state = self._get_initial_state(items)
        self.session_db.add_session_entry(
            default_session_state_key_fn(self, update), state
        )

        auth_level = get_auth_level_from_message(self.db, update)
        allow_edit = auth_level >= AuthLevels.MOD.value
        return self.create_message(state, full_redraw=True, allow_edit=allow_edit)

    @repaint
    @callback(
        cmds=[
            "goto",
            "tags",
            "addtag",
            "remtag",
            "seasons",
            "searchseason",
            "path",
            "selectpath",
            "quality",
            "selectquality",
            "language",
            "selectlanguage",
            "addmenu",
            "useseasonfolder",
            "selectuseseasonfolder",
        ]
    )
    @sessionState()
    @authorized(min_auth_level=AuthLevels.USER)
    async def clbk_update(self, update, context, args, state):
        auth_level = get_auth_level_from_message(self.db, update)
        allow_edit = auth_level >= AuthLevels.MOD.value
        # Prevent any changes from being made if in library and permission level below MOD
        if args[0] in [
            "addtag",
            "remtag",
            "selectpath",
            "selectquality",
            "selectlanguage",
            "searchseason",
        ]:
            item = state.items[state.index]
            if "id" in item and item["id"] and not allow_edit:
                # Don't do anything, illegal operation
                return Response(caption="You are missing the permissions for this operation.")

        full_redraw = False
        if args[0] == "goto":
            if len(args) > 1:
                idx = int(args[1])
                item = state.items[idx]
                state = replace(
                    state,
                    index=idx,
                    root_folder=find_first(
                        self.root_folders,
                        lambda x: item.get("folderName").startswith(x.get("path")),
                    ),
                    use_season_folder=item.get("seasonFolder"),
                    quality_profile=find_first(
                        self.quality_profiles,
                        lambda x: item.get("qualityProfileId") == x.get("id"),
                    ),
                    tags=item.get("tags", []),
                    menu=None,
                    seasons=self._get_season_state(item),
                )
                full_redraw = True
            else:
                state = replace(state, menu=None)
        elif args[0] == "seasons":
            state = replace(state, menu="seasons")
        elif args[0] == "searchseason":
            self.request(
                "command",
                action=Action.POST,
                params={
                    "name": "SeasonSearch",
                    "seriesId": item.get("id"),
                    "seasonNumber": args[1],
                },
            )
            new_selected = [*state.seasons.selected, int(args[1])]
            season_state = replace(state.seasons, selected=new_selected)
            state = replace(state, seasons=season_state)
        elif args[0] == "tags":
            state = replace(state, tags=[], menu="tags")
        elif args[0] == "addtag":
            state = replace(state, tags=[*state.tags, args[1]])
        elif args[0] == "remtag":
            state = replace(state, tags=[t for t in state.tags if t != args[1]])
        elif args[0] == "path":
            state = replace(state, menu="path")
        elif args[0] == "selectpath":
            path = self.get_root_folder(args[1])
            state = replace(state, root_folder=path, menu="add")
        elif args[0] == "quality":
            state = replace(state, menu="quality")
        elif args[0] == "selectquality":
            quality_profile = self.get_quality_profile(args[1])
            state = replace(state, quality_profile=quality_profile, menu="add")
        elif args[0] == "language":
            state = replace(state, menu="language")
        elif args[0] == "selectlanguage":
            language_profile = self.get_language_profile(args[1])
            state = replace(state, language_profile=language_profile, menu="add")
        elif args[0] == "addmenu":
            state = replace(state, menu="add")
        elif args[0] == "useseasonfolder":
            # state = replace(state, menu="useseasonfolder")
            # use_season_folder = eval(args[1])
            state = replace(state, use_season_folder=(not state.use_season_folder), menu="add")
        elif args[0] == "selectuseseasonfolder":
            use_season_folder = eval(args[1])
            state = replace(state, use_season_folder=use_season_folder, menu="add")


        return self.create_message(
            state, full_redraw=full_redraw, allow_edit=allow_edit
        )

    @clear
    @callback(cmds=["add"])
    @sessionState(clear=True)
    @authorized(min_auth_level=AuthLevels.USER)
    async def clbk_add(self, update, context, args, state):
        result = self.add(
            item=state.items[state.index],
            quality_profile_id=state.quality_profile.get("id", 0),
            language_profile_id=state.language_profile.get("id", 0),
            root_folder_path=state.root_folder.get("path", ""),
            tags=state.tags,
            options={
                "seasonFolder": state.use_season_folder,
                "addOptions": {
                    "searchForMissingEpisodes": args[1] == "search",
                    "monitor": "none" if args[1] == "no-monitor" else "all",
                },
            },
        )
        if not result:
            return Response(caption="Seems like something went wrong...")

        return Response(
            caption=(
                "Series updated!"
                if state.items[state.index].get("id")
                else "Series added!"
            )
        )

    @clear
    @callback(cmds=["cancel"])
    @sessionState(clear=True)
    @authorized(min_auth_level=AuthLevels.USER)
    async def clbk_cancel(self, update, context, args, state):
        return Response(caption="Search canceled!")

    @clear
    @callback(cmds=["remove"])
    @sessionState(clear=True)
    @authorized(min_auth_level=AuthLevels.USER)
    async def clbk_remove(self, update, context, args, state):
        self.remove(id=state.items[state.index].get("id"))
        return Response(caption="Series removed!")
