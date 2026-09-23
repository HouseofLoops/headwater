"""Constants shared by the Google Maps service modules."""
# Every request this service makes is a browser navigation to google.com/maps.
# A plain GET through the datacenter proxy succeeds, but a full Chromium page
# load times out: Google throttles the subresource fetches, so domcontentloaded
# never fires and the job fails after 60s. Direct, the same navigation returns
# in 0.4s. Routing by host lets Maps go direct while Reddit keeps the proxy.
GOOGLE_MAPS_HOST = "https://www.google.com"
