# Build customizations for the nvim-speaks NVDA add-on.

from site_scons.site_tools.NVDATool.typings import AddonInfo, BrailleTables, SpeechDictionaries, SymbolDictionaries
from site_scons.site_tools.NVDATool.utils import _


addon_info = AddonInfo(
	addon_name="nvimSpeaksServer",
	addon_summary=_("nvim-speaks NVDA server"),
	addon_description=_(
		"Runs a local TCP speech server inside NVDA for the nvim-speaks Neovim plugin.",
	),
	addon_version="0.1.0",
	addon_changelog=_("Initial in-process NVDA speech server."),
	addon_author="nvim-speaks contributors",
	addon_url=None,
	addon_sourceURL=None,
	addon_docFileName="readme.html",
	addon_minimumNVDAVersion="2024.1.0",
	addon_lastTestedNVDAVersion="2025.4.0",
	addon_updateChannel=None,
	addon_license="GPL-2.0-or-later",
	addon_licenseURL=None,
)

pythonSources: list[str] = ["addon/globalPlugins/**/*.py"]
i18nSources: list[str] = pythonSources + ["buildVars.py"]
excludedFiles: list[str] = []
baseLanguage: str = "en"
markdownExtensions: list[str] = []
brailleTables: BrailleTables = {}
symbolDictionaries: SymbolDictionaries = {}
speechDictionaries: SpeechDictionaries = {}
