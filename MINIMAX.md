# MINIMAX.md - Lessons Learned from Sidebar Implementation

## Issues Encountered and How to Solve Them Faster

### 1. Sidebar Positioning Issues

**Problem:** The sidebar uses `position: sticky` inside a flex container, which doesn't work reliably across browsers.

**Symptoms:**
- Sidebar appears at top of viewport instead of below header on first load
- Sidebar moves/scrolls incorrectly when navigating between pages

**What actually happened:**
- `position: sticky` inside a flex item (`layout--with-sidebar`) is problematic
- The sidebar was being pulled out of normal flow

**Solutions tried:**
1. `position: fixed` with `top: 0` - takes sidebar out of flex flow, content overlays sidebar
2. `position: sticky` - unreliable in flex containers, causes scroll issues
3. `height: 100vh` with `overflow-y: auto` - works but header overlaps

**Final working solution:**
- Keep sidebar as a normal flex item (no sticky, no fixed)
- Use grid/flex layout for the main layout container
- The sidebar naturally scrolls with the page since parent has `overflow-y: auto`

**Key insight:** The `.layout--with-sidebar` is a flex container. The sidebar's height (`100vh`) combined with the main column's content height can cause layout issues. Keep it simple - don't try to make the sidebar "stick" in a flex container.

### 2. Double Sidebar Issue

**Problem:** Two sidebars appeared - one from `base.html` (page-tree-sidebar) and one from `view.html` (toc-sidebar).

**Root cause:** Both templates were rendering their respective sidebars simultaneously.

**Solution:** Added `page_has_sidebar` flag:
- Set `page_has_sidebar=True` in `view_page()` route
- In `base.html`, only render page-tree-sidebar when `page_tree and not page_has_sidebar`
- In `view.html`, render toc-sidebar (which now shows page tree) when `page_tree`

### 3. CSS Flex vs Grid for Layout

**Problem:** Tags column header didn't align with tags column cells.

**Root cause:** The tags cell used `display: flex` for wrapping tags, but the header didn't, causing misalignment.

**Lesson:** If using flexbox for cell content, the header should also use flexbox with matching alignment.

### 4. Testing CSS Changes

**Problem:** CSS changes are cached by browser and hard to test interactively.

**Faster testing approach:**
- Use incognito/private window for each test
- Hard refresh (Cmd+Shift+R) to clear cache
- Check actual CSS being served with curl

### 5. Jinja2 Macro Scope

**Problem:** `render_tree` macro defined inside `{% if %}` block wasn't available when needed in `view.html`.

**Lesson:** Define Jinja2 macros at the top of the template (before any blocks) so they're available everywhere.

## Debugging Checklist for CSS/Layout Issues

1. **Check HTML structure first**
   ```bash
   curl -s http://localhost:8080/ | grep -E "class=\"(layout|sidebar)" 
   ```

2. **Check actual CSS being served**
   ```bash
   curl -s http://localhost:8080/static/css/style.css | grep -A10 "\.sidebar"
   ```

3. **Check if element has correct classes**
   - Browser DevTools > Elements > Check element classes

4. **Test in incognito** - avoids cached CSS/JS

5. **Hard refresh** - Cmd+Shift+R (Mac) or Ctrl+Shift+R (Windows)

## Common Sidebar Patterns

### Pattern 1: Sidebar + Main Content (No Sticky)
```css
.layout {
    display: flex;
    height: 100vh;
    overflow: hidden;
}
.sidebar {
    width: 260px;
    flex-shrink: 0;
    overflow-y: auto;
}
.main {
    flex: 1;
    overflow-y: auto;
}
```

### Pattern 2: Sidebar + Main Content (Sticky Header)
```css
.layout {
    display: grid;
    grid-template-columns: 260px 1fr;
    height: 100vh;
}
.sidebar {
    overflow-y: auto;
}
.main {
    overflow-y: auto;
}
```

## Testing Matrix

| Page | base.html sidebar | view.html sidebar | Should Show |
|------|-------------------|-------------------|-------------|
| / (home) | Yes (page_tree) | No | base.html sidebar |
| /page/X | No (page_has_sidebar=True) | Yes | view.html sidebar |

## Git Commands for This Branch

```bash
# Continue working on sidebar fix
git checkout fix/sidebar-css

# See all changes
git diff origin/main..HEAD

# Force push (after rebase if needed)
git push origin fix/sidebar-css --force-with-lease
```
# Deployment trigger
