# debrid-concierge

Small Windows tray app that sends magnet links and .torrent files to a debrid
service (TorBox first) and pulls the finished files down over plain HTTPS
through a download manager. The machine never joins the BitTorrent network,
so there is no torrent client, no peer traffic, and nothing for a campus or
office network to complain about. This is a privacy focused tool.

Status: early development, not usable yet. Built against TorBox's API and
AB Download Manager's local API.

License: MIT
