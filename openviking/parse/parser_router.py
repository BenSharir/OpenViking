# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
ParserRouter: Route parsing requests between ParserRegistry and KnowledgeParser.

Routing is controlled by environment variables.
"""
import os
from pathlib import Path
from typing import TYPE_CHECKING, Union

if TYPE_CHECKING:
    from openviking.parse.accessors.base import LocalResource

from openviking_cli.utils.logger import get_logger
from openviking.parse.base import ParseResult
from openviking.parse.registry import ParserRegistry

logger = get_logger(__name__)



class ParserRouter:
    """
    ParserRouter: Route parsing to internal ParserRegistry or third-party KnowledgeParser.

    Routing logic:
    1. Check feature flag and extension whitelist
    2. Default: ParserRegistry
    3. Matched extensions: KnowledgeParser
    """

    def __init__(self, parser_registry: ParserRegistry):
        self._parser_registry = parser_registry
        self._knowledge_parser = None

    def should_use_knowledge_parser(self, source_path: Union[str, Path]) -> bool:
        """
        Decide whether to use KnowledgeParser.

        Env example:
        - KNOWLEDGE_PARSER_ENABLED=true
        - KNOWLEDGE_PARSER_EXTENSIONS=pdf,docx,pptx,mp4,mp3
        """
        enabled = os.environ.get("KNOWLEDGE_PARSER_ENABLED", "false").lower() == "true"
        if not enabled:
            return False

        ext = Path(source_path).suffix.lower().lstrip(".")
        extensions_str = os.environ.get("KNOWLEDGE_PARSER_EXTENSIONS", "pdf,docx,pptx,mp4,mp3")
        extensions = [e.strip().lower() for e in extensions_str.split(",") if e.strip()]
        return ext in extensions

    async def parse(self, source: Union[str, Path, "LocalResource"], **kwargs) -> ParseResult:
        """
        Parse with ParserRegistry or KnowledgeParser based on the routing decision.
        """
        source_path = self._extract_source_path(source)

        if self.should_use_knowledge_parser(source_path):
            logger.info(f"[ParserRouter] Using KnowledgeParser for {source_path}")
            return await self._get_knowledge_parser().parse(str(source_path), **kwargs)
        else:
            logger.info(f"[ParserRouter] Using internal ParserRegistry for {source_path}")
            return await self._parser_registry.parse(source, **kwargs)

    def _extract_source_path(self, source: Union[str, Path, "LocalResource"]) -> Union[str, Path]:
        """Extract a filesystem path from the source."""
        if hasattr(source, "path"):
            return source.path
        return source

    def _get_knowledge_parser(self):
        if self._knowledge_parser is None:
            from openviking.parse.knowledge_parser import KnowledgeParser
            self._knowledge_parser = KnowledgeParser()
        return self._knowledge_parser
