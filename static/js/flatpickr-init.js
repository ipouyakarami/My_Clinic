function initCalendarPicker(input, options) {
    options = options || {};
    var defaults = {
        dateFormat: "Y/m/d",
        allowInput: true,
        disableMobile: true,
    };
    var config = Object.assign({}, defaults, options);
    if (input.value && input.value.match(/^\d{4}\/\d{2}\/\d{2}$/)) {
        config.defaultDate = input.value;
    }
    return flatpickr(input, config);
}

function initTimePicker(input, options) {
    options = options || {};
    var defaults = {
        enableTime: true,
        noCalendar: true,
        dateFormat: "H:i",
        time_24hr: true,
        allowInput: true,
        disableMobile: true,
    };
    var config = Object.assign({}, defaults, options);
    if (input.value && input.value.match(/^\d{1,2}:\d{2}$/)) {
        config.defaultDate = input.value;
    }
    return flatpickr(input, config);
}

function initTimeRangePicker(startInput, endInput, options) {
    options = options || {};
    var startDefaults = {
        enableTime: true,
        noCalendar: true,
        dateFormat: "H:i",
        time_24hr: true,
        allowInput: true,
        disableMobile: true,
    };
    var endDefaults = {
        enableTime: true,
        noCalendar: true,
        dateFormat: "H:i",
        time_24hr: true,
        allowInput: true,
        disableMobile: true,
        minDate: startInput.value || undefined,
    };
    if (options.onChange) {
        startDefaults.onChange = function(selectedDates, dateStr, instance) {
            endInput._flatpickr.set('minDate', dateStr);
            if (options.onChange) options.onChange(dateStr, endInput._flatpickr.input.value);
        };
    }
    var startConfig = Object.assign({}, startDefaults, options.startOptions);
    var endConfig = Object.assign({}, endDefaults, options.endOptions);
    if (startInput.value && startInput.value.match(/^\d{1,2}:\d{2}$/)) {
        startConfig.defaultDate = startInput.value;
    }
    if (endInput.value && endInput.value.match(/^\d{1,2}:\d{2}$/)) {
        endConfig.defaultDate = endInput.value;
    }
    var startFp = flatpickr(startInput, startConfig);
    var endFp = flatpickr(endInput, endConfig);
    startInput._flatpickr = startFp;
    endInput._flatpickr = endFp;
    return { start: startFp, end: endFp };
}
