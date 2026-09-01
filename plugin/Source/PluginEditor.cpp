// PluginEditor.cpp — see PluginEditor.h.
//
// Valhalla-grade skin, jamn palette: indigo section panels on
// near-black, black knobs with a white pointer + value readouts, the
// JamN logo (gradient waveform bars) drawn vectorially, pad waveforms
// with live playheads, and a MODE/PRESET-style footer strip.

#include "PluginEditor.h"

#include <thread>

namespace theme
{
const juce::Colour background { 0xff0b0b0f };
const juce::Colour panel { 0xff262157 };        // indigo section block
const juce::Colour panelDeep { 0xff1b1740 };
const juce::Colour panelStroke { 0xff4a44a8 };
const juce::Colour surface { 0xff17171d };
const juce::Colour stroke { 0xff2a2a33 };
const juce::Colour textPrimary { 0xffeef0ff };
const juce::Colour textSecondary { 0xff9aa0c0 };
const juce::Colour accent { 0xff8b5cf6 };
const juce::Colour accentBlue { 0xff3b82f6 };
const juce::Colour armedAmber { 0xfff59e0b };

const juce::Colour previewRows[4] = {
    juce::Colour(0xffef4444), juce::Colour(0xff22c55e),
    juce::Colour(0xfff59e0b), juce::Colour(0xfff97316),
};

constexpr int margin = 18;
constexpr int headerH = 64;
constexpr int clockH = 26;
constexpr int knobPanelH = 96;
constexpr int footerH = 30;
constexpr int gapS = 8, gapM = 12;
}  // namespace theme

// MARK: - Look & feel (black Valhalla-style knobs)

namespace
{
struct JamnLookAndFeel : juce::LookAndFeel_V4
{
    JamnLookAndFeel()
    {
        setColour(juce::PopupMenu::backgroundColourId, theme::panelDeep);
        setColour(juce::PopupMenu::textColourId, theme::textPrimary);
        setColour(juce::PopupMenu::highlightedTextColourId,
                  juce::Colours::white);
        setColour(juce::PopupMenu::highlightedBackgroundColourId,
                  theme::accent.withAlpha(0.45f));
    }

    void drawPopupMenuBackground(juce::Graphics& g, int w, int h) override
    {
        const auto r = juce::Rectangle<float>(0, 0, (float) w, (float) h);
        g.setColour(theme::panelDeep);
        g.fillRoundedRectangle(r, 9.0f);
        g.setColour(theme::panelStroke);
        g.drawRoundedRectangle(r.reduced(0.5f), 9.0f, 1.0f);
    }

    juce::Font getPopupMenuFont() override
    {
        return juce::Font(juce::FontOptions(13.0f));
    }

    void drawRotarySlider(juce::Graphics& g, int x, int y, int w, int h,
                          float pos, float startAngle, float endAngle,
                          juce::Slider&) override
    {
        auto bounds = juce::Rectangle<float>((float) x, (float) y,
                                             (float) w, (float) h)
                          .reduced(3.0f);
        const float size = juce::jmin(bounds.getWidth(), bounds.getHeight());
        auto knob = bounds.withSizeKeepingCentre(size, size);

        // Value arc behind the knob.
        const float angle = startAngle + pos * (endAngle - startAngle);
        juce::Path arc;
        arc.addCentredArc(knob.getCentreX(), knob.getCentreY(),
                          size * 0.5f, size * 0.5f, 0.0f,
                          startAngle, angle, true);
        g.setColour(theme::accent.withAlpha(0.9f));
        g.strokePath(arc, juce::PathStrokeType(2.4f,
                                               juce::PathStrokeType::curved,
                                               juce::PathStrokeType::rounded));

        // Black body.
        auto body = knob.reduced(size * 0.10f);
        g.setColour(juce::Colour(0xff0a0a10));
        g.fillEllipse(body);
        g.setColour(juce::Colours::white.withAlpha(0.16f));
        g.drawEllipse(body, 1.0f);

        // White pointer.
        const float r = body.getWidth() * 0.5f;
        juce::Path pointer;
        pointer.addRoundedRectangle(-1.4f, -r + 3.0f, 2.8f, r * 0.55f, 1.4f);
        g.setColour(juce::Colours::white);
        g.fillPath(pointer, juce::AffineTransform::rotation(angle)
                                .translated(body.getCentreX(),
                                            body.getCentreY()));
    }
};

JamnLookAndFeel& jamnLookAndFeel()
{
    static JamnLookAndFeel lnf;
    return lnf;
}

/// Human title from an analysis slug: dashes → spaces, the trailing
/// random suffix dropped, words capitalized.
/// "tycho-sea-lemon-anotherwave-official-visualiser-rmigek"
///   → "Tycho Sea Lemon Anotherwave Official Visualiser".
static juce::String prettySongName(const juce::String& slug)
{
    auto tokens = juce::StringArray::fromTokens(slug, "-", "");
    tokens.removeEmptyStrings();
    if (tokens.size() > 2 && tokens.strings.getLast().length() == 6
        && tokens.strings.getLast().containsOnly(
            "abcdefghijklmnopqrstuvwxyz0123456789"))
        tokens.remove(tokens.size() - 1);
    for (auto& t : tokens.strings)
        t = t.substring(0, 1).toUpperCase() + t.substring(1);
    const auto joined = tokens.joinIntoString(" ");
    return joined.isNotEmpty() ? joined : slug;
}

/// The JamN mark: gradient waveform bars, drawn vectorially so it is
/// crisp at any size. `area` is the square-ish glyph region.
void drawJamnBars(juce::Graphics& g, juce::Rectangle<float> area)
{
    static const float heights[] = { 0.38f, 0.66f, 1.0f, 0.80f,
                                     0.55f, 0.72f, 0.40f };
    constexpr int n = (int) std::size(heights);
    const float bw = area.getWidth() / (n * 1.6f);
    const float step = area.getWidth() / (float) n;
    juce::ColourGradient grad(theme::accent, area.getX(), 0,
                              theme::accentBlue, area.getRight(), 0, false);
    for (int i = 0; i < n; ++i)
    {
        const float h = heights[i] * area.getHeight();
        juce::Rectangle<float> bar(
            area.getX() + step * (float) i + (step - bw) * 0.5f,
            area.getCentreY() - h * 0.5f, bw, h);
        g.setColour(grad.getColourAtPosition(
            (bar.getCentreX() - area.getX()) / area.getWidth()));
        g.fillRoundedRectangle(bar, bw * 0.5f);
    }
}
}  // namespace

static void themeKnob(juce::Slider& s, juce::Label& l, const juce::String& name)
{
    s.setSliderStyle(juce::Slider::RotaryHorizontalVerticalDrag);
    s.setTextBoxStyle(juce::Slider::TextBoxBelow, false, 64, 14);
    s.setColour(juce::Slider::textBoxTextColourId, theme::textSecondary);
    s.setColour(juce::Slider::textBoxOutlineColourId,
                juce::Colours::transparentBlack);
    s.setLookAndFeel(&jamnLookAndFeel());
    l.setText(name, juce::dontSendNotification);
    l.setJustificationType(juce::Justification::centred);
    l.setColour(juce::Label::textColourId, theme::textPrimary);
    l.setFont(juce::Font(juce::FontOptions(10.0f, juce::Font::bold)));
}

// MARK: - Host-proof HTTP
//
// Inside a DAW, network calls inherit the HOST app's App Transport
// Security policy — Live blocks cleartext http:// via NSURLSession
// ("backend unreachable" with the backend demonstrably up), and the
// numeric 127.0.0.1 doesn't get the localhost exemption. For http://
// we therefore speak HTTP/1.0 over a raw socket (1.0 = no chunked
// encoding, read-until-close); https:// still goes through juce::URL.
// Returns the body; statusCode 0 = connect/parse failure.
static juce::MemoryBlock fetchHttp(const juce::String& urlString,
                                   int& statusCode, int timeoutMs)
{
    statusCode = 0;
    juce::URL url(urlString);
    if (url.getScheme() != "http")
    {
        juce::MemoryBlock body;
        if (auto stream = url.createInputStream(
                juce::URL::InputStreamOptions(
                    juce::URL::ParameterHandling::inAddress)
                    .withConnectionTimeoutMs(timeoutMs)
                    .withStatusCode(&statusCode)))
            stream->readIntoMemoryBlock(body);
        return body;
    }

    const auto host = url.getDomain();
    const int port = url.getPort() != 0 ? url.getPort() : 80;
    juce::String path = url.toString(true)
                            .fromFirstOccurrenceOf("//", false, false)
                            .fromFirstOccurrenceOf("/", false, false);
    path = "/" + path;

    juce::StreamingSocket socket;
    if (!socket.connect(host, port, timeoutMs))
        return {};
    const juce::String request =
        "GET " + path + " HTTP/1.0\r\nHost: " + host
        + "\r\nConnection: close\r\n\r\n";
    if (socket.write(request.toRawUTF8(),
                     (int) request.getNumBytesAsUTF8()) < 0)
        return {};

    juce::MemoryBlock raw;
    char buffer[1 << 16];
    for (;;)
    {
        // Long server renders send nothing for minutes; keep waiting
        // for readiness up to the caller's timeout per read.
        const int ready = socket.waitUntilReady(true, timeoutMs);
        if (ready <= 0)
            break;
        const int n = socket.read(buffer, sizeof(buffer), false);
        if (n <= 0)
            break;
        raw.append(buffer, (size_t) n);
    }

    const juce::String head =
        juce::String::fromUTF8((const char*) raw.getData(),
                               (int) juce::jmin(raw.getSize(), (size_t) 4096));
    statusCode = head.fromFirstOccurrenceOf(" ", false, false)
                     .upToFirstOccurrenceOf(" ", false, false)
                     .getIntValue();
    const char* data = (const char*) raw.getData();
    for (size_t i = 3; i < raw.getSize(); ++i)
        if (data[i - 3] == '\r' && data[i - 2] == '\n'
            && data[i - 1] == '\r' && data[i] == '\n')
        {
            juce::MemoryBlock body;
            body.append(data + i + 1, raw.getSize() - i - 1);
            return body;
        }
    return {};
}

JamnKitEditor::JamnKitEditor(JamnKitProcessor& p)
    : AudioProcessorEditor(p), processor(p)
{
    for (auto* b : { &openButton, &browseButton })
    {
        addAndMakeVisible(*b);
        b->setColour(juce::TextButton::buttonColourId, theme::panelDeep);
        b->setColour(juce::TextButton::textColourOffId, theme::textPrimary);
    }
    openButton.onClick = [this] { openPackChooser(); };
    browseButton.onClick = [this] { browseBackend(); };
    browseButton.setColour(juce::TextButton::buttonColourId,
                           theme::accent.withAlpha(0.45f));

    addAndMakeVisible(urlEditor);
    urlEditor.setColour(juce::TextEditor::backgroundColourId, theme::surface);
    urlEditor.setColour(juce::TextEditor::textColourId, theme::textSecondary);
    urlEditor.setColour(juce::TextEditor::outlineColourId, theme::stroke);
    urlEditor.setFont(juce::Font(juce::FontOptions(11.0f)));
    urlEditor.setText(processor.backendUrl(), juce::dontSendNotification);
    urlEditor.onTextChange = [this] {
        processor.setBackendUrl(urlEditor.getText().trim());
    };

    themeKnob(knobFilter, labelFilter, "FILTER");
    themeKnob(knobSpace, labelSpace, "SPACE");
    themeKnob(knobDrive, labelDrive, "DRIVE");
    themeKnob(knobGain, labelGain, "GAIN");
    for (auto* c : std::initializer_list<juce::Component*> {
             &knobFilter, &knobSpace, &knobDrive, &knobGain, &labelFilter,
             &labelSpace, &labelDrive, &labelGain })
        addAndMakeVisible(*c);
    attFilter = std::make_unique<Attachment>(processor.apvts, "filter", knobFilter);
    attSpace = std::make_unique<Attachment>(processor.apvts, "space", knobSpace);
    attDrive = std::make_unique<Attachment>(processor.apvts, "drive", knobDrive);
    attGain = std::make_unique<Attachment>(processor.apvts, "gain", knobGain);

    setResizable(true, true);
    setResizeLimits(440, 620, 1100, 1500);
    setSize(540, 780);
    startTimerHz(30);
}

JamnKitEditor::~JamnKitEditor()
{
    for (auto* s : { &knobFilter, &knobSpace, &knobDrive, &knobGain })
        s->setLookAndFeel(nullptr);
    if (worker != nullptr && worker->joinable())
        worker->join();
}

void JamnKitEditor::resized()
{
    using namespace theme;
    auto area = getLocalBounds().reduced(margin);
    auto header = area.removeFromTop(headerH);
    // Inset from the header panel's rounded edge — flush-right read as
    // touching the margin.
    auto buttons = header.reduced(14, 0)
                       .removeFromRight(170)
                       .withSizeKeepingCentre(170, 28);
    browseButton.setBounds(buttons.removeFromLeft(80));
    buttons.removeFromLeft(8);
    openButton.setBounds(buttons);

    area.removeFromTop(gapS + clockH + gapM);
    auto knobPanel = area.removeFromTop(knobPanelH).reduced(6, 8);
    urlEditor.setBounds(getLocalBounds()
                            .removeFromBottom(footerH)
                            .reduced(margin, 4)
                            .removeFromLeft(210));
    const int kw = knobPanel.getWidth() / 4;
    auto place = [&](juce::Slider& s, juce::Label& l, int i) {
        auto cell = juce::Rectangle<int>(knobPanel.getX() + i * kw,
                                         knobPanel.getY(), kw,
                                         knobPanel.getHeight());
        l.setBounds(cell.removeFromTop(13));
        s.setBounds(cell);
    };
    place(knobFilter, labelFilter, 0);
    place(knobSpace, labelSpace, 1);
    place(knobDrive, labelDrive, 2);
    place(knobGain, labelGain, 3);
}

// MARK: - Pack sources

void JamnKitEditor::openPackChooser()
{
    statusLine.clear();
    chooser = std::make_unique<juce::FileChooser>(
        "Open a JamN Kit pack (folder or zip)",
        juce::File::getSpecialLocation(juce::File::userHomeDirectory),
        "*.zip");
    const auto flags = juce::FileBrowserComponent::openMode
        | juce::FileBrowserComponent::canSelectFiles
        | juce::FileBrowserComponent::canSelectDirectories;
    chooser->launchAsync(flags, [this](const juce::FileChooser& fc) {
        const auto file = fc.getResult();
        if (file == juce::File())
            return;
        statusLine = processor.loadPack(file);
        repaint();
    });
}

void JamnKitEditor::browseBackend()
{
    if (busy)
        return;
    statusLine = "fetching songs...";
    busy = true;
    busyStartMs = juce::Time::currentTimeMillis();
    const juce::String base = processor.backendUrl();
    auto self = juce::Component::SafePointer<JamnKitEditor>(this);

    if (worker != nullptr && worker->joinable())
        worker->join();
    worker = std::make_unique<std::thread>([self, base] {
        int statusCode = 0;
        const auto raw =
            fetchHttp(base + "/api/history?limit=25", statusCode, 8000);
        const juce::String body = raw.toString();

        juce::MessageManager::callAsync([self, body, statusCode, base] {
            if (self == nullptr)
                return;
            self->busy = false;
            const auto parsed = juce::JSON::parse(body);
            auto* history = parsed.getProperty("history", {}).getArray();
            if (history == nullptr || history->isEmpty())
            {
                // Distinguish "server down" from "server empty" — the
                // blended message sent people hunting a URL typo when
                // the backend was just restarting.
                if (statusCode == 0)
                    self->statusLine = "backend unreachable: " + base
                        + " (is it running?)";
                else if (statusCode != 200)
                    self->statusLine = "backend error http "
                        + juce::String(statusCode);
                else
                    self->statusLine = "backend has no analyzed songs yet";
                self->repaint();
                return;
            }
            self->statusLine.clear();
            juce::PopupMenu menu;
            menu.setLookAndFeel(&jamnLookAndFeel());
            juce::StringArray ids, names;
            int itemId = 1;
            menu.addSectionHeader("JamN — your songs");
            for (const auto& entry : *history)
            {
                const auto name =
                    entry.getProperty("name", "song").toString();
                ids.add(entry.getProperty("id", "").toString());
                names.add(name);
                menu.addItem(itemId++,
                             prettySongName(name).substring(0, 44));
            }
            menu.showMenuAsync(
                juce::PopupMenu::Options()
                    .withTargetComponent(&self->browseButton)
                    .withStandardItemHeight(26)
                    .withMinimumWidth(260),
                [self, ids, names](int picked) {
                    if (self == nullptr || picked <= 0)
                        return;
                    self->downloadKit(ids[picked - 1], names[picked - 1]);
                });
            self->repaint();
        });
    });
}

void JamnKitEditor::downloadKit(const juce::String& entryId,
                                const juce::String& name)
{
    if (busy || entryId.isEmpty())
        return;
    busy = true;
    busyStartMs = juce::Time::currentTimeMillis();
    statusLine = "downloading kit: " + name.substring(0, 32) + "...";
    repaint();
    const juce::String base = processor.backendUrl();
    auto self = juce::Component::SafePointer<JamnKitEditor>(this);

    if (worker != nullptr && worker->joinable())
        worker->join();
    worker = std::make_unique<std::thread>([self, base, entryId] {
        auto dest = juce::File::getSpecialLocation(juce::File::tempDirectory)
                        .getChildFile("jamnKit")
                        .getChildFile("dl_" + entryId + ".zip");
        dest.getParentDirectory().createDirectory();

        // Legacy songs backfill their graph server-side on the first
        // hit — allow minutes, not seconds.
        int statusCode = 0;
        const auto raw = fetchHttp(
            base + "/api/song/" + entryId + "/ableton-kit?pads=16",
            statusCode, 600000);
        bool ok = statusCode == 200 && raw.getSize() > 0;
        if (ok)
            ok = dest.replaceWithData(raw.getData(), raw.getSize());

        juce::MessageManager::callAsync([self, dest, ok] {
            if (self == nullptr)
                return;
            self->busy = false;
            self->statusLine = ok ? self->processor.loadPack(dest)
                                  : juce::String("download failed");
            self->repaint();
        });
    });
}

// MARK: - Layout helpers

juce::Rectangle<int> JamnKitEditor::gridArea() const
{
    using namespace theme;
    auto area = getLocalBounds().reduced(margin);
    area.removeFromTop(headerH + gapS + clockH + gapM + knobPanelH + gapM);
    area.removeFromBottom(footerH + gapS);
    return area;
}

/// Pad cell geometry: cells STRETCH so the 4×4 fills the pads panel
/// edge-to-edge on both axes (no dead margins; cells go rectangular
/// as the window changes shape).
static void padCellGeometry(juce::Rectangle<int> panel, int& cellW,
                            int& cellH, int& x0, int& y0, int gap = 8)
{
    const auto area = panel.reduced(10);
    cellW = (area.getWidth() - 3 * gap) / 4;
    cellH = (area.getHeight() - 3 * gap) / 4;
    x0 = area.getX();
    y0 = area.getY();
}

int JamnKitEditor::padIndexAt(juce::Point<int> pos) const
{
    int cellW = 0, cellH = 0, x0 = 0, y0 = 0;
    const int gap = 8;
    padCellGeometry(gridArea(), cellW, cellH, x0, y0, gap);
    for (int row = 0; row < 4; ++row)
        for (int col = 0; col < 4; ++col)
        {
            juce::Rectangle<int> r(x0 + col * (cellW + gap),
                                   y0 + row * (cellH + gap), cellW, cellH);
            if (r.contains(pos))
                return (3 - row) * 4 + col;
        }
    return -1;
}

void JamnKitEditor::mouseDown(const juce::MouseEvent& e)
{
    const int pad = padIndexAt(e.getPosition());
    if (pad < 0)
        return;
    mousePad = pad;
    processor.noteOnFromUI(JamnKitProcessor::kFirstNote + pad);
}

void JamnKitEditor::mouseUp(const juce::MouseEvent&)
{
    if (mousePad < 0)
        return;
    processor.noteOffFromUI(JamnKitProcessor::kFirstNote + mousePad);
    mousePad = -1;
}

// MARK: - Paint

void JamnKitEditor::paint(juce::Graphics& g)
{
    using namespace theme;
    g.fillAll(background);
    auto area = getLocalBounds().reduced(margin);
    const auto pack = processor.currentPack();

    // Header panel: logo left, song + version right.
    auto header = area.removeFromTop(headerH);
    {
        juce::ColourGradient grad(panel, (float) header.getX(),
                                  (float) header.getY(), panelDeep,
                                  (float) header.getX(),
                                  (float) header.getBottom(), false);
        g.setGradientFill(grad);
        g.fillRoundedRectangle(header.toFloat(), 10.0f);
        g.setColour(panelStroke);
        g.drawRoundedRectangle(header.toFloat(), 10.0f, 1.0f);

        auto inner = header.reduced(14, 10);
        auto glyph = inner.removeFromLeft(40).toFloat().reduced(2.0f, 6.0f);
        drawJamnBars(g, glyph);
        inner.removeFromLeft(10);
        auto words = inner.removeFromLeft(120);
        g.setColour(textPrimary);
        g.setFont(juce::Font(juce::FontOptions(24.0f, juce::Font::bold)));
        g.drawText("JamN", words.removeFromTop(26),
                   juce::Justification::bottomLeft);
        g.setColour(textSecondary);
        g.setFont(juce::Font(juce::FontOptions(11.0f)));
        // Build stamp: instantly answers "is Live running the fresh
        // binary?" (hosts cache plugin dylibs for the whole session).
        g.drawText(juce::String("jamn.app  ·  b") + __TIME__, words,
                   juce::Justification::topLeft);

        g.setFont(juce::Font(juce::FontOptions(11.0f)));
        g.drawText(pack != nullptr ? pack->songName : juce::String("no pack"),
                   inner.withTrimmedRight(178),
                   juce::Justification::centredRight);
    }

    area.removeFromTop(gapS);

    // Clock strip with live bar sweep.
    auto clockRow = area.removeFromTop(clockH);
    const auto c = processor.hostClock();
    g.setColour(panelDeep);
    g.fillRoundedRectangle(clockRow.toFloat(), 7.0f);
    if (c.playing)
    {
        g.setColour(accent.withAlpha(0.30f));
        g.fillRoundedRectangle(
            clockRow.toFloat().withWidth(
                (float) clockRow.getWidth() * (float) c.barPhase),
            7.0f);
    }
    g.setColour(panelStroke.withAlpha(0.6f));
    g.drawRoundedRectangle(clockRow.toFloat(), 7.0f, 1.0f);
    g.setColour(c.playing ? textPrimary : textSecondary);
    g.setFont(juce::Font(juce::FontOptions(12.0f)));
    juce::String status = c.bpm > 0
        ? juce::String(c.bpm, 1) + " BPM  |  beat "
              + juce::String(c.ppqPosition, 1) + (c.playing ? "  >" : "  ||")
        : juce::String("host clock: waiting");
    if (pack != nullptr && pack->tempoBpm > 0)
        status << "     song " << juce::String(pack->tempoBpm, 0) << " BPM";
    g.drawText(status, clockRow.reduced(12, 0),
               juce::Justification::centredLeft);

    area.removeFromTop(gapM);

    // Macro panel behind the knobs.
    auto knobPanel = area.removeFromTop(knobPanelH);
    g.setColour(panel.withAlpha(0.85f));
    g.fillRoundedRectangle(knobPanel.toFloat(), 10.0f);
    g.setColour(panelStroke);
    g.drawRoundedRectangle(knobPanel.toFloat(), 10.0f, 1.0f);
    for (int i = 1; i < 4; ++i)
    {
        const float x = (float) knobPanel.getX()
            + (float) knobPanel.getWidth() * (float) i / 4.0f;
        g.setColour(panelStroke.withAlpha(0.35f));
        g.drawLine(x, (float) knobPanel.getY() + 10.0f, x,
                   (float) knobPanel.getBottom() - 10.0f, 1.0f);
    }

    // Pads panel.
    const auto gridPanel = gridArea();
    g.setColour(panelDeep.withAlpha(0.55f));
    g.fillRoundedRectangle(gridPanel.toFloat(), 10.0f);
    g.setColour(panelStroke.withAlpha(0.5f));
    g.drawRoundedRectangle(gridPanel.toFloat(), 10.0f, 1.0f);

    int cellW = 0, cellH = 0, x0 = 0, y0 = 0;
    const int gap = 8;
    padCellGeometry(gridPanel, cellW, cellH, x0, y0, gap);
    for (int row = 0; row < 4; ++row)
    {
        for (int col = 0; col < 4; ++col)
        {
            const int padIdx = (3 - row) * 4 + col;
            const int note = JamnKitProcessor::kFirstNote + padIdx;
            juce::Rectangle<int> r(x0 + col * (cellW + gap),
                                   y0 + row * (cellH + gap), cellW, cellH);

            const KitPadSample* pad =
                pack != nullptr ? pack->padForNote(note) : nullptr;
            const bool active = processor.isNoteActive(note);
            const bool armed = processor.isNoteArmed(note);

            juce::Colour base = pad != nullptr ? pad->colour()
                                               : previewRows[row];
            const bool empty = pack != nullptr && pad == nullptr;
            if (empty)
                base = surface;

            g.setColour(
                base.withAlpha(active ? 0.58f : (armed ? 0.40f : 0.24f)));
            g.fillRoundedRectangle(r.toFloat(), 9.0f);

            if (pad != nullptr && !pad->peaks.empty())
            {
                auto wf = r.reduced(8).removeFromBottom(r.getHeight() / 2 - 6);
                const float bw =
                    (float) wf.getWidth() / (float) pad->peaks.size();
                g.setColour(base.withAlpha(active ? 0.95f : 0.55f));
                for (size_t i = 0; i < pad->peaks.size(); ++i)
                {
                    const float h = juce::jmax(
                        1.0f, pad->peaks[i] * (float) wf.getHeight());
                    g.fillRect((float) wf.getX() + (float) i * bw,
                               (float) wf.getCentreY() - h * 0.5f,
                               juce::jmax(1.0f, bw - 1.0f), h);
                }
                const float phase =
                    processor.padPhase(note - JamnKitProcessor::kFirstNote);
                if (phase >= 0.0f)
                {
                    const float x =
                        (float) wf.getX() + phase * (float) wf.getWidth();
                    g.setColour(juce::Colours::white.withAlpha(0.9f));
                    g.fillRect(x, (float) wf.getY(), 1.5f,
                               (float) wf.getHeight());
                }
            }

            const juce::Colour ring = active
                ? juce::Colours::white.withAlpha(0.85f)
                : (armed ? armedAmber : panelStroke.withAlpha(0.5f));
            g.setColour(ring);
            g.drawRoundedRectangle(r.toFloat(), 9.0f,
                                   (active || armed) ? 1.6f : 1.0f);

            g.setColour(textPrimary.withAlpha(empty ? 0.35f : 0.92f));
            g.setFont(juce::Font(juce::FontOptions(11.0f, juce::Font::bold)));
            juce::String label = pad != nullptr
                ? pad->name
                : juce::MidiMessage::getMidiNoteName(note, true, true, 3);
            g.drawFittedText(label,
                             r.reduced(8).removeFromTop(r.getHeight() / 2 - 6),
                             juce::Justification::topLeft, 2);
        }
    }

    // Footer strip (MODE/PRESET row, jamn edition): URL field lives
    // left; status text right.
    juce::String footer = statusLine.isNotEmpty()
        ? statusLine
        : (pack != nullptr ? juce::String("tap pads: loops land on the bar")
                           : juce::String("Browse pulls kits from jamn"));
    if (busy && busyStartMs > 0)
    {
        const auto secs =
            (juce::Time::currentTimeMillis() - busyStartMs) / 1000;
        footer << "  " << juce::String(secs) << "s";
        if (secs > 15)
            footer << " (first export renders server-side; repeats instant)";
    }
    g.setColour(statusLine.isNotEmpty() && !busy
                    ? juce::Colour(0xffef4444)
                    : textSecondary);
    g.setFont(juce::Font(juce::FontOptions(11.0f)));
    g.drawText(footer,
               getLocalBounds()
                   .removeFromBottom(footerH)
                   .reduced(margin, 0)
                   .withTrimmedLeft(220),
               juce::Justification::centredRight);
}
