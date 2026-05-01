"""Regular expressions and parser constants for Blueprint text exports."""

from __future__ import annotations

import re

NOISE_FIELDS = {"NodePosX", "NodePosY", "NodeWidth", "NodeHeight", "ErrorType", "ErrorMsg"}

GUID_RE = re.compile(r"\b[0-9A-Fa-f]{32}\b")
CLASS_RE = re.compile(r"\bClass=(?:\"(?P<quoted>[^\"]+)\"|(?P<bare>\S+))")
NAME_RE = re.compile(r"\bName=\"(?P<name>[^\"]+)\"")
EXPORT_RE = re.compile(r"\bExportPath=\"(?P<path>[^\"]+)\"")
MEMBER_NAME_RE = re.compile(r"\bMemberName=\"(?P<name>[^\"]+)\"")
VAR_MEMBER_RE = re.compile(r"VariableReference=.*?\bMemberName=\"(?P<name>[^\"]+)\"")
DELEGATE_MEMBER_RE = re.compile(r"DelegateReference=.*?\bMemberName=\"(?P<name>[^\"]+)\"")
CUSTOM_FUNCTION_RE = re.compile(r"\bCustomFunctionName=\"(?P<name>[^\"]+)\"")
NODE_GUID_RE = re.compile(r"\bNodeGuid=(?P<guid>[A-Za-z0-9_]+)")
GRAPH_GUID_RE = re.compile(r"\bGraphGuid=(?P<guid>[A-Za-z0-9_]+)")
PIN_ID_RE = re.compile(r"\bPinId=(?P<id>[A-Za-z0-9_]+)")
PIN_NAME_RE = re.compile(r"\bPinName=\"(?P<name>[^\"]*)\"")
PIN_CATEGORY_RE = re.compile(r"\bPinType\.PinCategory=\"(?P<category>[^\"]*)\"")
PIN_SUBCATEGORY_RE = re.compile(r"\bPinType\.PinSubCategory=\"(?P<subcategory>[^\"]*)\"")
DIRECTION_RE = re.compile(r"\bDirection=\"?(?P<direction>EGPD_[A-Za-z]+)\"?")
DEFAULT_VALUE_RE = re.compile(r"\bDefault(?:Value|Object|TextValue)=(?P<value>\"[^\"]*\"|[^,\)]+)")
LINKED_TO_RE = re.compile(r"\bLinkedTo=\((?P<linked>.*?)\)")
PERSISTENT_GUID_RE = re.compile(r"\bPersistentGuid=(?P<guid>[A-Za-z0-9_]+)")
COMMENT_RE = re.compile(r"\b(?:NodeComment|CommentText)=\"(?P<comment>[^\"]*)\"")
MACRO_RE = re.compile(r"\bMacroGraph=\"[^\"]*:(?P<macro>[^'\"]+)")
MACRO_FALLBACK_RE = re.compile(r"\bMacroGraphReference=.*?(?:MacroGraph|Graph)=\"(?P<path>[^\"]+)\"")
