// The same login UI as main_widgets.cpp, in Qt Quick.
//
// The example suite runs the same scenario against both, so that "one API for widgets and QML"
// is a tested claim rather than a promise in a README.

import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

ApplicationWindow {
    id: root
    objectName: "loginWindow"
    title: "Login"
    width: 480
    height: 400
    visible: true

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 16
        spacing: 8

        TextField {
            id: usernameField
            objectName: "usernameField"
            placeholderText: "User"
            Layout.fillWidth: true
        }

        TextField {
            id: passwordField
            objectName: "passwordField"
            placeholderText: "Password"
            echoMode: TextInput.Password
            Layout.fillWidth: true
        }

        Button {
            id: submitButton
            objectName: "submitButton"
            text: "Log in"
            Layout.fillWidth: true
            onClicked: {
                if (usernameField.text.length === 0) {
                    statusLabel.text = "User name is required"
                    return
                }
                statusLabel.text = "Signing in..."
                signInTimer.start()
            }
        }

        Label {
            id: statusLabel
            objectName: "statusLabel"
            text: "Ready"
        }

        ListView {
            id: ordersList
            objectName: "ordersList"
            visible: false
            Layout.fillWidth: true
            Layout.fillHeight: true
            model: ListModel {
                ListElement { order: "INV-1041"; customer: "Acme"; total: "120.00" }
                ListElement { order: "INV-1042"; customer: "Globex"; total: "89.50" }
                ListElement { order: "INV-1043"; customer: "Initech"; total: "1450.00" }
            }
            delegate: Label {
                objectName: "orderRow"
                text: order + "  " + customer + "  " + total
            }
        }
    }

    Timer {
        id: signInTimer
        interval: 300
        onTriggered: {
            statusLabel.text = "Welcome, " + usernameField.text
            ordersList.visible = true
        }
    }
}
