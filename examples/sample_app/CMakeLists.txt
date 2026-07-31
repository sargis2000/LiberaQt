cmake_minimum_required(VERSION 3.16)
project(qtdriver_sample LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_AUTOMOC ON)
set(CMAKE_AUTORCC ON)

find_package(QT NAMES Qt6 Qt5 REQUIRED COMPONENTS Core Gui Widgets)
find_package(Qt${QT_VERSION_MAJOR} REQUIRED COMPONENTS Core Gui Widgets)
find_package(Qt${QT_VERSION_MAJOR} QUIET COMPONENTS Quick Qml QuickWidgets)

# Widget sample: a login form plus an orders table. Every interactive widget has an objectName,
# which is exactly the discipline the docs ask users to adopt.
add_executable(sample_widgets main_widgets.cpp)
target_link_libraries(sample_widgets PRIVATE
  Qt${QT_VERSION_MAJOR}::Core Qt${QT_VERSION_MAJOR}::Gui Qt${QT_VERSION_MAJOR}::Widgets)

# QML sample: the same UI in Qt Quick, so the test suite can prove that one API covers both.
if(Qt${QT_VERSION_MAJOR}Quick_FOUND)
  add_executable(sample_quick main_quick.cpp qml.qrc)
  target_link_libraries(sample_quick PRIVATE
    Qt${QT_VERSION_MAJOR}::Core Qt${QT_VERSION_MAJOR}::Gui
    Qt${QT_VERSION_MAJOR}::Quick Qt${QT_VERSION_MAJOR}::Qml)
endif()
