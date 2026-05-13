// src/screens/algo_trading/PropfirmPanel.h
#pragma once

#include <QJsonArray>
#include <QLabel>
#include <QNetworkAccessManager>
#include <QObject>
#include <QPushButton>
#include <QScrollArea>
#include <QTimer>
#include <QVBoxLayout>
#include <QWidget>

namespace fincept::screens {

/// Read-only Propfirm ATA panel.
///
/// Polls the local propfirm Fusion panel `/alerts` endpoint and renders sanitized
/// TradingView webhook facts inside Fincept's native Obsidian theme. This widget
/// intentionally has no order buttons, no broker imports, and no webhook secret UI.
class PropfirmPanel : public QWidget {
    Q_OBJECT
  public:
    explicit PropfirmPanel(QWidget* parent = nullptr);

  public slots:
    void refresh();
    void start_polling();
    void stop_polling();

  private:
    QWidget* build_header();
    QWidget* build_stat_card(const QString& label, const QString& value, QLabel** out_label);
    QWidget* build_event_card(const QJsonObject& event);
    void render_events(const QJsonArray& events, int event_count, const QString& generated_at);
    void render_empty(const QString& message);
    void set_status(const QString& text, const QString& color);
    QString endpoint_base() const;
    QString field_text(const QJsonObject& obj, const QString& key, const QString& fallback = QStringLiteral("—")) const;
    QString compact_json(const QJsonObject& obj) const;
    QString card_border_color(const QString& status, const QString& kind) const;

    QNetworkAccessManager* nam_ = nullptr;
    QTimer* poll_timer_ = nullptr;
    QLabel* status_label_ = nullptr;
    QLabel* event_count_label_ = nullptr;
    QLabel* latest_label_ = nullptr;
    QLabel* generated_label_ = nullptr;
    QLabel* endpoint_label_ = nullptr;
    QScrollArea* scroll_area_ = nullptr;
    QWidget* list_container_ = nullptr;
    QVBoxLayout* list_layout_ = nullptr;
    QPushButton* refresh_btn_ = nullptr;
    QPushButton* open_panel_btn_ = nullptr;
};

} // namespace fincept::screens
