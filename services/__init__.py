# -*- coding: utf-8 -*-
"""服务层"""
from services.cross_db import CrossDBService, SongWithUserContext, PlaylistWithSongs

__all__ = ["CrossDBService", "SongWithUserContext", "PlaylistWithSongs"]
