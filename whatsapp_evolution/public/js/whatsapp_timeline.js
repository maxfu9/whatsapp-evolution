(() => {
    const WA_BADGE_SVG = `
        <svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
            <path fill="currentColor" d="M20.52 3.48A11.86 11.86 0 0 0 12.06 0C5.43 0 .03 5.4.03 12.03c0 2.12.56 4.2 1.61 6.03L0 24l6.13-1.6a12 12 0 0 0 5.93 1.51h.01c6.63 0 12.03-5.4 12.03-12.03a11.9 11.9 0 0 0-3.58-8.4zM12.07 21.9h-.01a9.9 9.9 0 0 1-5.04-1.38l-.36-.21-3.64.95.97-3.55-.24-.37a9.94 9.94 0 0 1-1.53-5.3c0-5.5 4.47-9.97 9.97-9.97 2.66 0 5.17 1.03 7.05 2.91a9.9 9.9 0 0 1 2.92 7.06c0 5.5-4.48 9.97-9.99 9.97zm5.47-7.47c-.3-.15-1.78-.88-2.06-.98-.27-.1-.47-.15-.67.15-.2.3-.77.97-.95 1.17-.17.2-.35.23-.65.08-.3-.15-1.27-.47-2.43-1.49-.9-.8-1.52-1.8-1.7-2.1-.18-.3-.02-.46.13-.6.13-.13.3-.35.45-.52.15-.18.2-.3.3-.5.1-.2.05-.38-.02-.53-.08-.15-.67-1.6-.92-2.2-.24-.58-.49-.5-.67-.5h-.57c-.2 0-.52.08-.8.38-.27.3-1.04 1.02-1.04 2.5s1.07 2.9 1.22 3.1c.15.2 2.1 3.2 5.08 4.48.71.31 1.26.49 1.69.63.71.23 1.35.2 1.86.12.56-.08 1.78-.73 2.03-1.44.25-.7.25-1.3.17-1.43-.08-.13-.27-.2-.57-.35z"/>
        </svg>
    `;

    function decorate_whatsapp_timeline() {
        $(".wa-timeline-item").each(function () {
            const $waItem = $(this);
            const $timelineItem = $waItem.closest(".timeline-item");
            if (!$timelineItem.length || $timelineItem.attr("data-wa-decorated")) {
                return;
            }
            $timelineItem.attr("data-wa-decorated", "1");
            const $badge = $timelineItem.find(".timeline-badge").first();
            if ($badge.length) {
                $badge.attr("title", __("WhatsApp"));
                $badge.addClass("wa-badge");
                $badge.html(WA_BADGE_SVG);
            }
        });
    }

    $(document).on("app_ready", function () {
        decorate_whatsapp_timeline();

        const root = document.body;
        if (!root) {
            return;
        }
        const observer = new MutationObserver(() => decorate_whatsapp_timeline());
        observer.observe(root, { childList: true, subtree: true });
    });
})();
