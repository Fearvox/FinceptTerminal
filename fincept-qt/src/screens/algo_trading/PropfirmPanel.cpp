// src/screens/algo_trading/PropfirmPanel.cpp
#include "screens/algo_trading/PropfirmPanel.h"

#include "ui/theme/Theme.h"

#include <QDateTime>
#include <QDesktopServices>
#include <QFrame>
#include <QHBoxLayout>
#include <QJsonDocument>
#include <QJsonObject>
#include <QNetworkReply>
#include <QNetworkRequest>
#include <QPlainTextEdit>
#include <QRegularExpression>
#include <QUrl>
#include <QUrlQuery>

namespace fincept::screens {

namespace {

inline QString mono_font() {
    return QString("font-family:%1;").arg(fincept::ui::fonts::DATA_FAMILY());
}

inline QString label_style() {
    return QString("color:%1; font-size:9px; font-weight:700; letter-spacing:0.8px; "
                   "background:transparent; border:none; %2")
        .arg(fincept::ui::colors::TEXT_TERTIARY())
        .arg(mono_font());
}

inline QString value_style(const QString& color) {
    return QString("color:%1; font-size:14px; font-weight:700; background:transparent; border:none; %2")
        .arg(color)
        .arg(mono_font());
}

inline QString pill_style(const QString& color) {
    return QString("color:%1; background:rgba(217,119,6,0.08); border:1px solid %2; "
                   "padding:3px 8px; font-size:10px; font-weight:700; %3")
        .arg(color)
        .arg(fincept::ui::colors::BORDER_MED())
        .arg(mono_font());
}

void clear_layout(QLayout* layout) {
    if (!layout)
        return;
    while (auto* item = layout->takeAt(0)) {
        if (auto* child = item->widget())
            child->deleteLater();
        if (auto* child_layout = item->layout())
            clear_layout(child_layout);
        delete item;
    }
}

} // namespace

PropfirmPanel::PropfirmPanel(QWidget* parent) : QWidget(parent) {
    nam_ = new QNetworkAccessManager(this);
    poll_timer_ = new QTimer(this);
    poll_timer_->setInterval(2000);
    connect(poll_timer_, &QTimer::timeout, this, &PropfirmPanel::refresh);

    auto* root = new QVBoxLayout(this);
    root->setContentsMargins(12, 12, 12, 12);
    root->setSpacing(10);
    setStyleSheet(QString("background:%1;").arg(ui::colors::BG_BASE()));

    root->addWidget(build_header());

    auto* stats = new QWidget(this);
    stats->setStyleSheet("background:transparent;");
    auto* stats_l = new QHBoxLayout(stats);
    stats_l->setContentsMargins(0, 0, 0, 0);
    stats_l->setSpacing(8);
    stats_l->addWidget(build_stat_card("EVENTS", "0", &event_count_label_));
    stats_l->addWidget(build_stat_card("LATEST", "—", &latest_label_));
    stats_l->addWidget(build_stat_card("GENERATED", "—", &generated_label_));
    QLabel* boundary = nullptr;
    stats_l->addWidget(build_stat_card("BOUNDARY", "NO TRADES", &boundary));
    root->addWidget(stats);

    scroll_area_ = new QScrollArea(this);
    scroll_area_->setWidgetResizable(true);
    scroll_area_->setFrameShape(QFrame::NoFrame);
    scroll_area_->setStyleSheet(QString("QScrollArea{border:none;background:%1;}"
                                        "QScrollBar:vertical{width:5px;background:transparent;}"
                                        "QScrollBar::handle:vertical{background:%2;min-height:20px;}")
                                    .arg(ui::colors::BG_BASE(), ui::colors::BORDER_MED()));
    list_container_ = new QWidget(scroll_area_);
    list_container_->setStyleSheet("background:transparent;");
    list_layout_ = new QVBoxLayout(list_container_);
    list_layout_->setContentsMargins(0, 0, 0, 0);
    list_layout_->setSpacing(8);
    scroll_area_->setWidget(list_container_);
    root->addWidget(scroll_area_, 1);

    auto* footer = new QLabel(
        "NO TRADES EXECUTED, PLACED, OR SUGGESTED. This panel reads sanitized local webhook facts only.", this);
    footer->setWordWrap(true);
    footer->setStyleSheet(QString("color:%1; font-size:10px; background:%2; border:1px solid %3; padding:8px; %4")
                              .arg(ui::colors::TEXT_TERTIARY())
                              .arg(ui::colors::BG_SURFACE())
                              .arg(ui::colors::BORDER_DIM())
                              .arg(mono_font()));
    root->addWidget(footer);

    render_empty("Start `python3 -m propfirm_engine.fusion_stack`, then this panel will mirror /alerts.");
}

QWidget* PropfirmPanel::build_header() {
    auto* header = new QWidget(this);
    header->setStyleSheet(QString("background:%1; border:1px solid %2;")
                              .arg(ui::colors::BG_RAISED(), ui::colors::BORDER_DIM()));
    auto* hl = new QHBoxLayout(header);
    hl->setContentsMargins(12, 10, 12, 10);
    hl->setSpacing(12);

    auto* title_col = new QWidget(header);
    title_col->setStyleSheet("background:transparent; border:none;");
    auto* tl = new QVBoxLayout(title_col);
    tl->setContentsMargins(0, 0, 0, 0);
    tl->setSpacing(2);

    auto* title = new QLabel("PROPFIRM ATA", title_col);
    title->setStyleSheet(QString("color:%1; font-size:16px; font-weight:800; letter-spacing:1.2px; "
                                 "background:transparent; border:none; %2")
                             .arg(ui::colors::AMBER())
                             .arg(mono_font()));
    tl->addWidget(title);

    auto* sub = new QLabel("TradingView alert feed · deterministic accountability · local read-only", title_col);
    sub->setStyleSheet(QString("color:%1; font-size:10px; background:transparent; border:none; %2")
                           .arg(ui::colors::TEXT_TERTIARY())
                           .arg(mono_font()));
    tl->addWidget(sub);
    hl->addWidget(title_col, 1);

    endpoint_label_ = new QLabel(endpoint_base(), header);
    endpoint_label_->setStyleSheet(label_style());
    hl->addWidget(endpoint_label_);

    status_label_ = new QLabel("OFFLINE", header);
    status_label_->setStyleSheet(pill_style(ui::colors::WARNING()));
    hl->addWidget(status_label_);

    refresh_btn_ = new QPushButton("REFRESH", header);
    refresh_btn_->setCursor(Qt::PointingHandCursor);
    refresh_btn_->setStyleSheet(pill_style(ui::colors::CYAN()));
    connect(refresh_btn_, &QPushButton::clicked, this, &PropfirmPanel::refresh);
    hl->addWidget(refresh_btn_);

    open_panel_btn_ = new QPushButton("OPEN LOCAL PANEL", header);
    open_panel_btn_->setCursor(Qt::PointingHandCursor);
    open_panel_btn_->setStyleSheet(pill_style(ui::colors::TEXT_PRIMARY()));
    connect(open_panel_btn_, &QPushButton::clicked, this, [this]() {
        QDesktopServices::openUrl(QUrl(endpoint_base() + "/fusion-panel"));
    });
    hl->addWidget(open_panel_btn_);

    return header;
}

QWidget* PropfirmPanel::build_stat_card(const QString& label, const QString& value, QLabel** out_label) {
    auto* card = new QWidget(this);
    card->setStyleSheet(QString("background:%1; border:1px solid %2;")
                            .arg(ui::colors::BG_SURFACE(), ui::colors::BORDER_DIM()));
    auto* vl = new QVBoxLayout(card);
    vl->setContentsMargins(10, 8, 10, 8);
    vl->setSpacing(4);

    auto* l = new QLabel(label, card);
    l->setStyleSheet(label_style());
    vl->addWidget(l);

    auto* v = new QLabel(value, card);
    v->setStyleSheet(value_style(ui::colors::TEXT_PRIMARY()));
    v->setTextInteractionFlags(Qt::TextSelectableByMouse);
    vl->addWidget(v);

    if (out_label)
        *out_label = v;
    return card;
}

QString PropfirmPanel::endpoint_base() const {
    const QByteArray env = qgetenv("FINCEPT_PROPFIRM_PANEL_URL");
    QString base = env.isEmpty() ? QStringLiteral("http://127.0.0.1:5556") : QString::fromUtf8(env);
    while (base.endsWith('/'))
        base.chop(1);
    return base;
}

void PropfirmPanel::start_polling() {
    refresh();
    poll_timer_->start();
}

void PropfirmPanel::stop_polling() {
    poll_timer_->stop();
}

void PropfirmPanel::refresh() {
    QUrl url(endpoint_base() + "/alerts");
    QUrlQuery query;
    query.addQueryItem("limit", "40");
    url.setQuery(query);

    QNetworkRequest req(url);
    req.setAttribute(QNetworkRequest::RedirectPolicyAttribute, QNetworkRequest::NoLessSafeRedirectPolicy);
    req.setTransferTimeout(2500);
    auto* reply = nam_->get(req);
    connect(reply, &QNetworkReply::finished, this, [this, reply]() {
        reply->deleteLater();
        if (reply->error() != QNetworkReply::NoError) {
            set_status("OFFLINE", ui::colors::WARNING());
            render_empty("Local Propfirm panel is offline. Start `python3 -m propfirm_engine.fusion_stack`.");
            return;
        }

        const QJsonDocument doc = QJsonDocument::fromJson(reply->readAll());
        if (!doc.isObject()) {
            set_status("FLAG", ui::colors::WARNING());
            render_empty("/alerts did not return a JSON object.");
            return;
        }

        const QJsonObject payload = doc.object();
        render_events(
            payload.value("events").toArray(),
            payload.value("event_count").toInt(),
            payload.value("generated_at_utc").toString());
    });
}

void PropfirmPanel::render_events(const QJsonArray& events, int event_count, const QString& generated_at) {
    set_status("LIVE READ-ONLY", ui::colors::POSITIVE());
    event_count_label_->setText(QString::number(event_count));
    generated_label_->setText(generated_at.isEmpty() ? QStringLiteral("—") : generated_at.mid(11, 8));
    latest_label_->setText(events.isEmpty() ? QStringLiteral("—") : events.last().toObject().value("ts").toString().mid(11, 8));

    clear_layout(list_layout_);
    if (events.isEmpty()) {
        render_empty("No TradingView alerts logged yet. The panel is live, but the feed is empty.");
        return;
    }

    for (int i = events.size() - 1; i >= 0; --i) {
        list_layout_->addWidget(build_event_card(events.at(i).toObject()));
    }
    list_layout_->addStretch(1);
}

void PropfirmPanel::render_empty(const QString& message) {
    clear_layout(list_layout_);
    auto* empty = new QLabel(message, list_container_);
    empty->setAlignment(Qt::AlignCenter);
    empty->setWordWrap(true);
    empty->setStyleSheet(QString("color:%1; background:%2; border:1px dashed %3; padding:24px; font-size:12px; %4")
                             .arg(ui::colors::TEXT_TERTIARY())
                             .arg(ui::colors::BG_SURFACE())
                             .arg(ui::colors::BORDER_MED())
                             .arg(mono_font()));
    list_layout_->addWidget(empty);
    list_layout_->addStretch(1);
}

QWidget* PropfirmPanel::build_event_card(const QJsonObject& event) {
    const QString kind = field_text(event, "kind");
    const QString status = field_text(event, "status");
    const QString border = card_border_color(status, kind);

    auto* card = new QWidget(list_container_);
    card->setStyleSheet(QString("background:%1; border:1px solid %2; border-left:3px solid %3;")
                            .arg(ui::colors::BG_SURFACE(), ui::colors::BORDER_DIM(), border));
    auto* vl = new QVBoxLayout(card);
    vl->setContentsMargins(10, 8, 10, 8);
    vl->setSpacing(7);

    auto* top = new QHBoxLayout;
    auto* kind_lbl = new QLabel(kind.toUpper(), card);
    kind_lbl->setStyleSheet(value_style(border));
    top->addWidget(kind_lbl);
    top->addStretch(1);
    auto* ts = new QLabel(field_text(event, "ts"), card);
    ts->setStyleSheet(label_style());
    top->addWidget(ts);
    vl->addLayout(top);

    auto* fields = new QHBoxLayout;
    fields->setSpacing(6);
    const QStringList labels = {"symbol", "action", "event", "side", "price", "status"};
    for (const auto& key : labels) {
        QLabel* out = nullptr;
        fields->addWidget(build_stat_card(key.toUpper(), field_text(event, key), &out));
        if (out)
            out->setStyleSheet(value_style(key == "status" ? border : ui::colors::TEXT_PRIMARY()));
    }
    vl->addLayout(fields);

    auto* raw = new QPlainTextEdit(card);
    raw->setReadOnly(true);
    raw->setMaximumHeight(120);
    raw->setPlainText(compact_json(event));
    raw->setStyleSheet(QString("QPlainTextEdit{background:%1; color:%2; border:1px solid %3; font-size:10px; padding:6px; %4}")
                           .arg(ui::colors::BG_BASE())
                           .arg(ui::colors::TEXT_SECONDARY())
                           .arg(ui::colors::BORDER_DIM())
                           .arg(mono_font()));
    vl->addWidget(raw);

    return card;
}

void PropfirmPanel::set_status(const QString& text, const QString& color) {
    status_label_->setText(text);
    status_label_->setStyleSheet(pill_style(color));
}

QString PropfirmPanel::field_text(const QJsonObject& obj, const QString& key, const QString& fallback) const {
    const QJsonValue value = obj.value(key);
    if (value.isUndefined() || value.isNull())
        return fallback;
    if (value.isString()) {
        const QString s = value.toString();
        return s.isEmpty() ? fallback : s;
    }
    if (value.isDouble())
        return QString::number(value.toDouble(), 'f', 2).remove(QRegularExpression("\\.00$"));
    if (value.isBool())
        return value.toBool() ? "true" : "false";
    return QString::fromUtf8(QJsonDocument(value.toObject()).toJson(QJsonDocument::Compact));
}

QString PropfirmPanel::compact_json(const QJsonObject& obj) const {
    QJsonObject clipped;
    clipped["payload"] = obj.value("payload");
    clipped["response"] = obj.value("response");
    clipped["error"] = obj.value("error");
    return QString::fromUtf8(QJsonDocument(clipped).toJson(QJsonDocument::Indented));
}

QString PropfirmPanel::card_border_color(const QString& status, const QString& kind) const {
    const QString joined = (status + " " + kind).toLower();
    if (joined.contains("reject") || joined.contains("error"))
        return ui::colors::NEGATIVE();
    if (joined.contains("ignore") || joined.contains("flag"))
        return ui::colors::WARNING();
    return ui::colors::POSITIVE();
}

} // namespace fincept::screens
