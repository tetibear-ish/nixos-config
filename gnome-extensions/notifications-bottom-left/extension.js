import Clutter from 'gi://Clutter';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Layout from 'resource:///org/gnome/shell/ui/layout.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';

const MARGIN = 20;

export default class NotificationsBottomLeft extends Extension {
    enable() {
        const tray = Main.messageTray;
        // GNOME pins the message tray to the primary monitor with this constraint.
        this._constraint = tray.get_constraints().find(c => c instanceof Layout.MonitorConstraint);
        this._bin = tray._bannerBin;
        this._saved = {
            xAlign: tray.bannerAlignment,
            yAlign: this._bin.y_align,
            translationX: this._bin.translation_x,
            translationY: this._bin.translation_y,
        };

        tray.bannerAlignment = Clutter.ActorAlign.START;
        this._bin.y_align = Clutter.ActorAlign.END;
        // GNOME sets the banner's position directly, which overrides margins,
        // so offset it from the corner with a translation instead.
        this._bin.translation_x = MARGIN;
        this._bin.translation_y = -MARGIN;

        this._monitorsChangedId = Main.layoutManager.connect('monitors-changed',
            () => this._moveToLeftmostMonitor());
        this._moveToLeftmostMonitor();
    }

    _moveToLeftmostMonitor() {
        const monitors = Main.layoutManager.monitors;
        if (monitors.length === 0)
            return;
        const leftmost = monitors.reduce((a, b) => (b.x < a.x ? b : a));
        this._constraint.index = leftmost.index;
    }

    disable() {
        Main.layoutManager.disconnect(this._monitorsChangedId);
        this._constraint.primary = true;

        Main.messageTray.bannerAlignment = this._saved.xAlign;
        this._bin.y_align = this._saved.yAlign;
        this._bin.translation_x = this._saved.translationX;
        this._bin.translation_y = this._saved.translationY;

        this._constraint = null;
        this._bin = null;
        this._saved = null;
    }
}
