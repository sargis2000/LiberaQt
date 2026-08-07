// Sample QWidget application used by the example tests and by CI.
//
// Deliberately conventional: a login form, a status label, and a table. Note that every
// interactive widget gets an objectName -- that one habit is what makes a UI automatable.

#include <QApplication>
#include <QFormLayout>
#include <QHeaderView>
#include <QLabel>
#include <QLineEdit>
#include <QMainWindow>
#include <QMenuBar>
#include <QPushButton>
#include <QStandardItemModel>
#include <QTableView>
#include <QTimer>
#include <QVBoxLayout>
#include <QWidget>

class LoginWindow : public QMainWindow
{
    Q_OBJECT
public:
    LoginWindow()
    {
        setWindowTitle(QStringLiteral("Login"));
        setObjectName(QStringLiteral("loginWindow"));

        auto *central = new QWidget(this);
        auto *layout = new QVBoxLayout(central);

        auto *form = new QFormLayout;
        m_username = new QLineEdit(central);
        m_username->setObjectName(QStringLiteral("usernameField"));
        m_password = new QLineEdit(central);
        m_password->setObjectName(QStringLiteral("passwordField"));
        m_password->setEchoMode(QLineEdit::Password);
        form->addRow(QStringLiteral("User"), m_username);
        form->addRow(QStringLiteral("Password"), m_password);
        layout->addLayout(form);

        m_submit = new QPushButton(QStringLiteral("Log in"), central);
        m_submit->setObjectName(QStringLiteral("submitButton"));
        layout->addWidget(m_submit);

        m_status = new QLabel(QStringLiteral("Ready"), central);
        m_status->setObjectName(QStringLiteral("statusLabel"));
        layout->addWidget(m_status);

        m_table = new QTableView(central);
        m_table->setObjectName(QStringLiteral("ordersTable"));
        m_table->setModel(buildModel());
        m_table->horizontalHeader()->setStretchLastSection(true);
        m_table->hide();
        layout->addWidget(m_table);

        setCentralWidget(central);

        auto *fileMenu = menuBar()->addMenu(QStringLiteral("&File"));
        fileMenu->addAction(QStringLiteral("Quit"), qApp, &QApplication::quit);

        connect(m_submit, &QPushButton::clicked, this, &LoginWindow::onSubmit);
    }

private slots:
    void onSubmit()
    {
        if (m_username->text().isEmpty()) {
            m_status->setText(QStringLiteral("User name is required"));
            return;
        }
        m_status->setText(QStringLiteral("Signing in..."));
        // A deliberate async delay, so the example tests exercise auto-waiting rather than
        // passing by accident on a synchronous UI.
        QTimer::singleShot(300, this, [this] {
            m_status->setText(QStringLiteral("Welcome, %1").arg(m_username->text()));
            m_table->show();
        });
    }

private:
    static QStandardItemModel *buildModel()
    {
        auto *model = new QStandardItemModel(3, 3);
        model->setHorizontalHeaderLabels({QStringLiteral("Order"), QStringLiteral("Customer"),
                                          QStringLiteral("Total")});
        const char *rows[3][3] = {
            {"INV-1041", "Acme", "120.00"},
            {"INV-1042", "Globex", "89.50"},
            {"INV-1043", "Initech", "1450.00"},
        };
        for (int r = 0; r < 3; ++r)
            for (int c = 0; c < 3; ++c)
                model->setItem(r, c, new QStandardItem(QString::fromLatin1(rows[r][c])));
        return model;
    }

    QLineEdit *m_username = nullptr;
    QLineEdit *m_password = nullptr;
    QPushButton *m_submit = nullptr;
    QLabel *m_status = nullptr;
    QTableView *m_table = nullptr;
};

int main(int argc, char **argv)
{
    QApplication app(argc, argv);
    app.setApplicationName(QStringLiteral("liberaqt-sample-widgets"));
    LoginWindow window;
    window.resize(480, 400);
    window.show();
    return app.exec();
}

#include "main_widgets.moc"
