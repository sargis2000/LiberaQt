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
  src/menu_walker.cpp
  src/screenshot.cpp
  src/recorder.cpp
  src/idle_tracker.cpp
  src/signal_waiter.cpp
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

# Give this build a plugin key no other ABI can claim.
#
# Qt binds a plugin key to exactly one library -- qLoadPlugin takes the first match on
# QT_PLUGIN_PATH and never tries a second -- so two builds sharing a key cannot coexist on one
# path: the first directory wins and every other process gets nothing, silently. Pointer size
# alone is not enough to tell them apart, because a 64-bit MinGW plugin and a 64-bit MSVC plugin
# are equally unloadable in each other's process. Qt version, pointer size and compiler together
# are, so the key carries all three:
#
#   liberaqt_5_15_64_msvc     liberaqt_6_7_64_gnu     liberaqt_5_15_32_msvc
#
# Keep this in step with plugin_key_for() in src/liberaqt/agent_registry.py, which has to derive
# the same string from an install tag.
if(CMAKE_SIZEOF_VOID_P EQUAL 8)
  set(LIBERAQT_ABI_BITS 64)
else()
  set(LIBERAQT_ABI_BITS 32)
endif()

if(MSVC)
  set(LIBERAQT_ABI_COMPILER msvc)
elseif(CMAKE_CXX_COMPILER_ID STREQUAL "Clang")
  set(LIBERAQT_ABI_COMPILER clang)
else()
  set(LIBERAQT_ABI_COMPILER gnu)
endif()

string(REGEX MATCH "^([0-9]+)\\.([0-9]+)" _liberaqt_qt_match "${Qt${QT_VERSION_MAJOR}_VERSION}")
set(LIBERAQT_PLUGIN_KEY
    "liberaqt_${CMAKE_MATCH_1}_${CMAKE_MATCH_2}_${LIBERAQT_ABI_BITS}_${LIBERAQT_ABI_COMPILER}")
message(STATUS "liberaqt agent: plugin key ${LIBERAQT_PLUGIN_KEY}")

configure_file(src/liberaqt_plugin.json.in
               "${CMAKE_CURRENT_BINARY_DIR}/liberaqt_plugin.json" @ONLY)
# moc resolves Q_PLUGIN_METADATA's FILE against the target's include directories, so the
# generated metadata has to be reachable through one of them.
target_include_directories(liberaqt PRIVATE "${CMAKE_CURRENT_BINARY_DIR}")

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
