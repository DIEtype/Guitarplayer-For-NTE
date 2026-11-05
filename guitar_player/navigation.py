"""Explicit window presentation for the desktop editing workflow."""


def present(window,focus=None,modal=False):
    """Restore hidden/minimized windows before raising and restoring input focus."""
    window.deiconify();window.lift()
    window._root()._navigation_active=window
    if window.winfo_viewable() and not window.attributes('-disabled'):
        if modal:window.grab_set()
        (focus or window).focus_set()
    return window


def active_page(root):
    """Dialogs opened from a work page must not follow the hidden main window."""
    window=getattr(root,'_navigation_active',None)
    return window if window is not None and window.winfo_exists() else root


def navigate(owner,window,focus=None,modal=False,hide_owner=False):
    """Reuse work windows alongside the main controls; hide only intermediates."""
    if owner is window:return present(window,focus,modal)
    ancestor=owner;seen=set()
    while ancestor is not None and id(ancestor) not in seen:
        if ancestor is window:
            if owner.grab_current() is owner:owner.grab_release()
            owner.withdraw();return present(window,focus,modal)
        seen.add(id(ancestor));ancestor=getattr(ancestor,'_navigation_owner',None)
    window.transient('');window._navigation_owner=owner;window._navigation_modal=modal
    window._navigation_return=True
    if not getattr(window,'_navigation_bound',False):
        def closed(event):
            if event.widget is not window or not getattr(window,'_navigation_return',True):return
            root=window._root();previous=getattr(window,'_navigation_owner',None)
            if getattr(root,'_closing',False) or previous is None or not previous.winfo_exists():return
            focus=getattr(previous,'canvas',None) if getattr(previous,'_navigation_modal',False) else None
            present(previous,focus,getattr(previous,'_navigation_modal',False))
        window.bind('<Destroy>',closed,add='+');window._navigation_bound=True
    if owner.winfo_exists():
        if owner.grab_current() is owner:owner.grab_release()
        if hide_owner and owner is not window._root():owner.withdraw()
    return present(window,focus,modal)


def suppress_return(window):
    """A successful onward transition will present its own destination."""
    window._navigation_return=False
