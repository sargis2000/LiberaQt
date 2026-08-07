cmake_minimum_required(VERSION 3.16)
project(liberaqt_agent LANGUAGES CXX VERSION 0.1.0)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_AUTOMOC ON)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)

# The agent is a Qt plugin, so it must be built against the SAME Qt minor version and the SAME
# compiler/stdlib as the application under test. Point CMAKE_PREFIX_PATH at that Qt install.
#   cmake -S agent -B build/agent -DCMAKE_PREFIX_PATH=/opt/Qt/6.7.2/gcc_64
find_package(QT NAMES Qt6 Qt5 REQUIRED COMPONENTS Core Gui Widgets Network)
find_package(Qt${QT_VERSION_MAJOR} REQUIRED COMPONENTS Core Gui Widgets Network)

# Qt Quick support is optional at build time: an agent built without it still drives widget apps.
find_package(Qt${QT_VERSION_MAJOR} QUIET COMPONENTS Quick Qml QuickTest)
set(LIBERAQT_HAVE_QUICK ${Qt${QT_VERSION_MAJOR}Quick_FOUND})

message(STATUS "liberaqt agent: Qt ${QT_VERSION} (${CMAKE_CXX_COMPILER_ID}), quick=${LIBERAQT_HAVE_QUICK}")

if(QT_VERSION VERSION_LESS 5.15)
  message(FATAL_ERROR "liberaqt requires Qt 5.15 or newer")
endif()

set(LIBERAQT_SOURCES
  src/agent_plugin.cpp
  src/agent.cpp
  src/server.cpp
  src/dispatcher.cpp
  src/object_registry.cpp
  src/meta_invoke.cpp
  src/selector_engine.cpp
  src/widget_backend.cpp
  src/input_synth.cpp
  src/screenshot.cpp
  src/recorder.cpp
  src/idle_tracker.cpp
  src/value_codec.cpp
)

if(LIBERAQT_HAVE_QUICK)
  list(APPEND LIBERAQT_SOURCES src/quick_backend.cpp)
endif()

add_library(liberaqt MODULE ${LIBERAQT_SOURCES})

target_include_directories(liberaqt PRIVATE src)
target_link_libraries(liberaqt PRIVATE
  Qt${QT_VERSION_MAJOR}::Core
  Qt${QT_VERSION_MAJOR}::Gui
  Qt${QT_VERSION_MAJOR}::Widgets
  Qt${QT_VERSION_MAJOR}::Network
)

# Private headers give access to QWindowSystemInterface, which is how we deliver input events that
# behave like real ones. It is a private API: guarded, version-checked, and isolated in compat.h.
target_link_libraries(liberaqt PRIVATE Qt${QT_VERSION_MAJOR}::GuiPrivate)

if(LIBERAQT_HAVE_QUICK)
  target_link_libraries(liberaqt PRIVATE Qt${QT_VERSION_MAJOR}::Quick Qt${QT_VERSION_MAJOR}::Qml)
  target_compile_definitions(liberaqt PRIVATE LIBERAQT_HAVE_QUICK=1)
endif()

target_compile_definitions(liberaqt PRIVATE
  LIBERAQT_VERSION="${PROJECT_VERSION}"
  LIBERAQT_PROTOCOL=1
  QT_NO_CAST_FROM_ASCII
)

if(MSVC)
  target_compile_options(liberaqt PRIVATE /W4 /permissive-)
else()
  target_compile_options(liberaqt PRIVATE -Wall -Wextra -Wpedantic -fvisibility=hidden)
endif()

# Qt looks for generic plugins in <QT_PLUGIN_PATH>/generic/
set_target_properties(liberaqt PROPERTIES
  LIBRARY_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/plugins/generic"
  RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/plugins/generic"
)
if(WIN32)
  # No "lib" prefix on Windows; Qt looks for generic/liberaqt.dll.
  set_target_properties(liberaqt PROPERTIES PREFIX "")
endif()

install(TARGETS liberaqt
  LIBRARY DESTINATION plugins/generic
  RUNTIME DESTINATION plugins/generic
)

# Optional: the header-only embed shim for apps that link the agent themselves.
install(FILES src/embed.h DESTINATION include/liberaqt)
