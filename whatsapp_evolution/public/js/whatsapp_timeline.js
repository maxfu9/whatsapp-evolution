frappe.provide("frappe.views");

const TICK_SVGS = {
    "Sent": `<svg viewBox="0 0 16 16" xmlns="http://www.w3.org/2000/svg"><path d="M14.5 3.5L5.5 12.5L1.5 8.5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>`,
    "Delivered": `<svg viewBox="0 0 16 16" xmlns="http://www.w3.org/2000/svg"><path d="M10 3.5L3.5 10L1 7.5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/><path d="M15 3.5L8.5 10L6.5 8" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>`,
    "Read": `<svg viewBox="0 0 16 16" xmlns="http://www.w3.org/2000/svg"><path d="M10 3.5L3.5 10L1 7.5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/><path d="M15 3.5L8.5 10L6.5 8" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>`,
    "Error": `<svg viewBox="0 0 16 16" xmlns="http://www.w3.org/2000/svg"><circle cx="8" cy="8" r="7" fill="none" stroke="#e74c3c" stroke-width="2"/><line x1="8" y1="5" x2="8" y2="9" stroke="#e74c3c" stroke-width="2"/><circle cx="8" cy="11.5" r="1" fill="#e74c3c"/></svg>`
};

$(document).on("app_ready", function () {
    // Extend timeline item rendering
    if (frappe.views.Timeline) {
        let old_get_item_html = frappe.views.Timeline.prototype.get_item_html;
        frappe.views.Timeline.prototype.get_item_html = function (item) {
            let html = old_get_item_html.apply(this, arguments);

            if (item.doctype === "Communication" && item.communication_medium === "WhatsApp") {
                let $html = $(`<div>${html}</div>`);
                let $title = $html.find(".timeline-item-title");

                if ($title.length) {
                    let status = item.delivery_status || "Sent";
                    let icon_html = TICK_SVGS[status] || TICK_SVGS["Sent"];
                    let color_class = status === "Read" ? "status-read" : "";

                    let $indicator = $(`
                        <span class="whatsapp-status-indicator ${color_class}" data-status="${status}">
                            ${icon_html}
                        </span>
                    `);

                    $title.append($indicator);
                    html = $html.html();
                }
            }
            return html;
        };
    }
});
